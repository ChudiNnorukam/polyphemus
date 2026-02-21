"""SignalScorer — XGBoost signal quality classifier.

Scores each momentum signal 0-100 based on historical feature patterns.
Operates in two modes:

1. SHADOW mode (default): Scores every signal but doesn't filter.
   Logs scores to signal_logger for later analysis.

2. ACTIVE mode: Filters signals below a configurable threshold.
   Only activated after sufficient training data (200+ labeled signals).

The model trains on data from SignalLogger's signals.db, using
walk-forward validation to prevent overfitting.

Dependencies: xgboost, scikit-learn, numpy (installed on VPS via pip)
Inference latency: <5ms per signal (XGBoost is microsecond-class)
"""

import time
import pickle
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from .config import setup_logger

# Lazy imports — these may not be installed yet
_xgb = None
_np = None


def _ensure_imports():
    """Lazy-load ML dependencies."""
    global _xgb, _np
    if _xgb is None:
        try:
            import xgboost as xgb
            import numpy as np
            _xgb = xgb
            _np = np
        except ImportError:
            return False
    return True


class SignalScorer:
    """XGBoost-based signal quality scorer.

    Integrates with SignalLogger to get training data and
    with the signal flow to score incoming signals.
    """

    MIN_TRAINING_SAMPLES = 30  # Minimum labeled signals to train
    RETRAIN_INTERVAL = 3600    # Retrain every hour if new data available

    def __init__(
        self,
        signal_logger,
        model_path: str = "data/signal_model.pkl",
        mode: str = "shadow",
        threshold: float = 30.0,
    ):
        """Initialize signal scorer.

        Args:
            signal_logger: SignalLogger instance for training data.
            model_path: Path to persist trained model.
            mode: "shadow" (score but don't filter) or "active" (filter).
            threshold: Minimum score to pass in active mode (0-100).
        """
        self._logger = setup_logger("polyphemus.signal_scorer")
        self._signal_logger = signal_logger
        self._model_path = Path(model_path)
        self._model_path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._threshold = threshold
        self._model = None
        self._last_train_time = 0
        self._train_samples = 0
        self._has_deps = _ensure_imports()

        if not self._has_deps:
            self._logger.warning(
                "xgboost/numpy not installed — scorer disabled. "
                "Install with: pip install xgboost numpy scikit-learn"
            )
        else:
            self._load_model()

        self._logger.info(
            f"SignalScorer initialized: mode={mode}, threshold={threshold}, "
            f"deps_available={self._has_deps}, "
            f"model_loaded={self._model is not None}"
        )

    def _load_model(self):
        """Load a previously trained model from disk."""
        if self._model_path.exists():
            try:
                with open(self._model_path, "rb") as f:
                    saved = pickle.load(f)
                self._model = saved["model"]
                self._train_samples = saved.get("train_samples", 0)
                self._last_train_time = saved.get("train_time", 0)
                self._logger.info(
                    f"Loaded model trained on {self._train_samples} samples"
                )
            except Exception as e:
                self._logger.warning(f"Failed to load model: {e}")

    def _save_model(self):
        """Persist the trained model to disk."""
        try:
            with open(self._model_path, "wb") as f:
                pickle.dump({
                    "model": self._model,
                    "train_samples": self._train_samples,
                    "train_time": self._last_train_time,
                }, f)
            self._logger.info(f"Model saved to {self._model_path}")
        except Exception as e:
            self._logger.error(f"Failed to save model: {e}")

    def maybe_retrain(self):
        """Check if retraining is needed and train if so.

        Called periodically (e.g., from health monitor or signal loop).
        Only retrains if:
        - ML dependencies available
        - Enough new labeled data since last train
        - Sufficient time elapsed since last train
        """
        if not self._has_deps:
            return

        now = time.time()
        if now - self._last_train_time < self.RETRAIN_INTERVAL:
            return

        data = self._signal_logger.get_training_data(
            min_signals=self.MIN_TRAINING_SAMPLES
        )
        if data is None:
            return

        if len(data) <= self._train_samples:
            return  # No new data

        self._train(data)

    def _train(self, data: list):
        """Train the XGBoost model on labeled signal data.

        Uses walk-forward split: 80% train, 20% test (chronological).
        """
        np = _np
        xgb = _xgb

        # Data is list of tuples: (features..., is_win)
        arr = np.array(data, dtype=np.float32)
        X = arr[:, :-1]  # All columns except last
        y = arr[:, -1]   # is_win column

        # Handle NaN values
        X = np.nan_to_num(X, nan=0.0)

        # Walk-forward split (chronological, no shuffle)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        if len(X_train) < 20 or len(X_test) < 5:
            self._logger.warning(
                f"Not enough data for train/test split: "
                f"train={len(X_train)}, test={len(X_test)}"
            )
            return

        # Train XGBoost
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dtest = xgb.DMatrix(X_test, label=y_test)

        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": 4,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 3,
            "seed": 42,
            "verbosity": 0,
        }

        self._model = xgb.train(
            params,
            dtrain,
            num_boost_round=100,
            evals=[(dtest, "test")],
            early_stopping_rounds=10,
            verbose_eval=False,
        )

        # Evaluate
        preds = self._model.predict(dtest)
        pred_labels = (preds > 0.5).astype(int)
        accuracy = (pred_labels == y_test).mean()
        win_rate_pred = pred_labels.mean()

        # Feature importance
        feature_names = self._signal_logger.get_feature_columns()
        importance = self._model.get_score(importance_type="gain")
        top_features = sorted(
            importance.items(), key=lambda x: x[1], reverse=True
        )[:5]

        self._train_samples = len(data)
        self._last_train_time = time.time()
        self._save_model()

        self._logger.info(
            f"Model trained: samples={len(data)}, accuracy={accuracy:.1%}, "
            f"test_wr={win_rate_pred:.1%}, "
            f"top_features={[f[0] for f in top_features]}"
        )

    def score(self, features: Dict[str, float]) -> float:
        """Score a signal from 0-100.

        Args:
            features: Dict matching SignalLogger feature columns.

        Returns:
            Score 0-100 (higher = more likely to be profitable).
            Returns 50.0 if model not available.
        """
        if not self._has_deps or self._model is None:
            return 50.0  # Neutral score when no model

        np = _np
        xgb = _xgb

        # Build feature vector in correct order
        feature_cols = self._signal_logger.get_feature_columns()
        vec = []
        for col in feature_cols:
            vec.append(features.get(col, 0.0))

        X = np.array([vec], dtype=np.float32)
        X = np.nan_to_num(X, nan=0.0)
        dmatrix = xgb.DMatrix(X)

        prob = self._model.predict(dmatrix)[0]
        score = round(float(prob) * 100, 1)

        return score

    def should_trade(self, score: float) -> bool:
        """Determine if a signal passes the quality threshold.

        In shadow mode, always returns True (score is logged but not used).
        In active mode, returns True only if score >= threshold.
        """
        if self._mode == "shadow":
            return True
        return score >= self._threshold

    def build_feature_dict(
        self,
        momentum_pct: float = 0.0,
        midpoint: float = 0.0,
        spread: float = 0.0,
        book_imbalance: float = 0.0,
        time_remaining_secs: int = 0,
        hour_utc: int = 0,
        day_of_week: int = 0,
        volatility_1h: float = 0.0,
        trend_1h: float = 0.0,
        asset: str = "",
        direction: str = "",
        market_window_secs: int = 300,
    ) -> Dict[str, float]:
        """Build a feature dict matching the training schema.

        Convenience method that handles one-hot encoding of asset/direction.
        """
        return {
            "momentum_pct": momentum_pct,
            "midpoint": midpoint,
            "spread": spread,
            "book_imbalance": book_imbalance,
            "time_remaining_secs": time_remaining_secs,
            "hour_utc": hour_utc,
            "day_of_week": day_of_week,
            "volatility_1h": volatility_1h,
            "trend_1h": trend_1h,
            "is_btc": 1.0 if asset.upper() == "BTC" else 0.0,
            "is_eth": 1.0 if asset.upper() == "ETH" else 0.0,
            "is_sol": 1.0 if asset.upper() == "SOL" else 0.0,
            "is_up": 1.0 if direction.lower() == "up" else 0.0,
            "market_window_secs": float(market_window_secs),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get scorer stats for dashboard."""
        return {
            "mode": self._mode,
            "threshold": self._threshold,
            "model_loaded": self._model is not None,
            "deps_available": self._has_deps,
            "train_samples": self._train_samples,
            "last_train_age_secs": (
                round(time.time() - self._last_train_time, 0)
                if self._last_train_time > 0 else None
            ),
        }
