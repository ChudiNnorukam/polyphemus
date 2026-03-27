"""trader_stats.py — Statistical primitives for /ds CODE-tier sub-skills.

Implements: hypothesis testing, Wilson CI, Bayesian update, regression wrappers,
time series (ACF/ADF), classification, validation (walk-forward, DSR), Kelly criterion.

All functions are pure (no side effects, no DB access). They take numeric inputs
and return structured dicts. ~400 LOC.
"""

import math
from typing import Optional


# ============================================================
#  HYPOTHESIS TESTING (/ds:hypothesis) — STAT 20
# ============================================================

def hypothesis_test_wr(
    wins: int,
    total: int,
    breakeven: float = 0.50,
    alpha: float = 0.05,
    alternative: str = "greater",
) -> dict:
    """Z-test for win rate vs breakeven, with Wilson CI and effect size.

    Args:
        wins: number of winning trades
        total: total trades
        breakeven: null hypothesis WR (fee-adjusted breakeven)
        alpha: significance level
        alternative: "greater", "less", or "two-sided"

    Returns:
        dict with z_statistic, p_value, wilson_ci, effect_size, r8_label, etc.
    """
    from scipy import stats as sp_stats

    if total == 0:
        return {
            "test": "z-test for proportions",
            "observed_wr": 0.0,
            "null_wr": breakeven,
            "z_statistic": 0.0,
            "p_value": 1.0,
            "significant": False,
            "wilson_ci": (0.0, 0.0),
            "effect_size_cohens_h": 0.0,
            "effect_magnitude": "none",
            "r8_label": "ANECDOTAL",
            "n": 0,
            "plain_english": "No trades to analyze.",
        }

    p_hat = wins / total
    p0 = breakeven
    se = math.sqrt(p0 * (1 - p0) / total)

    if se == 0:
        z = 0.0
    else:
        z = (p_hat - p0) / se

    if alternative == "greater":
        p_value = 1 - sp_stats.norm.cdf(z)
    elif alternative == "less":
        p_value = sp_stats.norm.cdf(z)
    else:
        p_value = 2 * (1 - sp_stats.norm.cdf(abs(z)))

    ci = wilson_ci(wins, total)
    h = effect_size_cohens_h(p_hat, p0)

    if abs(h) < 0.2:
        magnitude = "negligible"
    elif abs(h) < 0.5:
        magnitude = "small"
    elif abs(h) < 0.8:
        magnitude = "medium"
    else:
        magnitude = "large"

    r8 = _r8_label(total)
    sig = p_value < alpha

    pct_wr = f"{p_hat * 100:.1f}%"
    pct_be = f"{p0 * 100:.1f}%"
    ci_str = f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]"
    p_str = f"{p_value:.6f}" if p_value >= 0.001 else "< 0.001"

    plain = (
        f"WR of {pct_wr} (n={total}) is "
        f"{'significantly' if sig else 'not significantly'} "
        f"{'above' if alternative == 'greater' else 'different from'} "
        f"breakeven {pct_be} (p={p_str}). "
        f"95% Wilson CI: {ci_str}. "
        f"{magnitude.capitalize()} effect size (Cohen's h={h:.2f}). "
        f"{r8} sample."
    )

    return {
        "test": "z-test for proportions",
        "observed_wr": round(p_hat, 4),
        "null_wr": breakeven,
        "z_statistic": round(z, 4),
        "p_value": round(p_value, 8),
        "significant": sig,
        "wilson_ci": (round(ci[0], 4), round(ci[1], 4)),
        "effect_size_cohens_h": round(h, 4),
        "effect_magnitude": magnitude,
        "r8_label": r8,
        "n": total,
        "plain_english": plain,
    }


def wilson_ci(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = wins / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return (max(0.0, center - spread), min(1.0, center + spread))


def effect_size_cohens_h(p1: float, p2: float) -> float:
    """Cohen's h effect size for two proportions."""
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def bonferroni_correct(p_values: list[float], alpha: float = 0.05) -> list[dict]:
    """Apply Bonferroni correction to a list of p-values."""
    k = len(p_values)
    adjusted_alpha = alpha / k if k > 0 else alpha
    return [
        {"original_p": p, "adjusted_alpha": adjusted_alpha, "significant": p < adjusted_alpha}
        for p in p_values
    ]


# ============================================================
#  BAYESIAN (/ds:bayesian) — DATA 102
# ============================================================

def beta_binomial_update(
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    wins: int = 0,
    losses: int = 0,
) -> dict:
    """Beta-Binomial conjugate update.

    Args:
        prior_alpha: Beta prior alpha (uninformative = 1.0)
        prior_beta: Beta prior beta
        wins: observed wins
        losses: observed losses

    Returns:
        dict with prior, posterior, HDI, predictive, prior influence assessment.
    """
    from scipy import stats as sp_stats

    post_alpha = prior_alpha + wins
    post_beta = prior_beta + losses
    n = wins + losses

    prior_mean = prior_alpha / (prior_alpha + prior_beta)
    post_mean = post_alpha / (post_alpha + post_beta)

    # Mode (only defined when alpha, beta > 1)
    if post_alpha > 1 and post_beta > 1:
        post_mode = (post_alpha - 1) / (post_alpha + post_beta - 2)
    else:
        post_mode = post_mean

    # 95% HDI via scipy
    dist = sp_stats.beta(post_alpha, post_beta)
    hdi_low, hdi_high = dist.ppf(0.025), dist.ppf(0.975)

    # Posterior predictive
    predictive = post_mean

    # Prior influence assessment
    if n == 0:
        influence = "TOTAL (no data yet)"
    elif n < 10:
        data_only_mean = wins / n if n > 0 else 0.5
        shift = abs(post_mean - data_only_mean)
        influence = f"HIGH (n={n}, prior shifts mean by {shift * 100:.1f}%)"
    elif n < 30:
        influence = f"MODERATE (n={n})"
    else:
        influence = f"LOW (n={n}, data dominates)"

    plain = (
        f"After {wins}W/{losses}L, estimated true WR is {post_mean * 100:.1f}% "
        f"(95% HDI: {hdi_low * 100:.1f}%-{hdi_high * 100:.1f}%). "
        f"{'Small sample - collect more data.' if n < 30 else 'Data-dominated estimate.'}"
    )

    return {
        "prior": {"alpha": prior_alpha, "beta": prior_beta, "mean": round(prior_mean, 4)},
        "observed": {"wins": wins, "losses": losses},
        "posterior": {
            "alpha": post_alpha,
            "beta": post_beta,
            "mean": round(post_mean, 4),
            "mode": round(post_mode, 4),
        },
        "hdi_95": (round(hdi_low, 4), round(hdi_high, 4)),
        "posterior_predictive": round(predictive, 4),
        "prior_influence": influence,
        "plain_english": plain,
    }


def posterior_predictive(alpha: float, beta: float) -> float:
    """P(next trade wins | posterior)."""
    return alpha / (alpha + beta)


# ============================================================
#  REGRESSION (/ds:regression) — ECON 140
# ============================================================

def logistic_regression(df, target: str, features: list[str]) -> dict:
    """Logistic regression with HC1 robust SE and VIF check.

    Args:
        df: pandas DataFrame
        target: binary target column name
        features: list of feature column names

    Returns:
        dict with coefficients, pseudo_r2, vif, interpretation.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    X = df[features].values.astype(float)
    y = df[target].values.astype(float)

    # sklearn for predictions
    lr = LogisticRegression(max_iter=1000, solver="lbfgs")
    lr.fit(X, y)

    # statsmodels for inference (HC1 robust SE)
    X_sm = sm.add_constant(X)
    try:
        logit = sm.Logit(y, X_sm).fit(disp=0, cov_type="HC1")
        p_values = logit.pvalues[1:]  # skip constant
        std_errors = logit.bse[1:]
        pseudo_r2 = logit.prsquared
    except Exception:
        p_values = [1.0] * len(features)
        std_errors = [0.0] * len(features)
        pseudo_r2 = 0.0

    # VIF
    vif = {}
    if X.shape[1] > 1:
        for i, feat in enumerate(features):
            try:
                vif[feat] = round(variance_inflation_factor(X, i), 2)
            except Exception:
                vif[feat] = float("nan")
    else:
        vif[features[0]] = 1.0

    coefficients = {}
    for i, feat in enumerate(features):
        coefficients[feat] = {
            "beta": round(float(lr.coef_[0][i]), 4),
            "se": round(float(std_errors[i]) if i < len(std_errors) else 0.0, 4),
            "p_value": round(float(p_values[i]) if i < len(p_values) else 1.0, 6),
            "significant": float(p_values[i]) < 0.05 if i < len(p_values) else False,
        }

    # Build interpretation
    sig_feats = [f for f, c in coefficients.items() if c["significant"]]
    if sig_feats:
        parts = []
        for f in sig_feats:
            c = coefficients[f]
            direction = "increases" if c["beta"] > 0 else "decreases"
            parts.append(f"{f} {direction} win probability (beta={c['beta']}, p={c['p_value']})")
        interpretation = ". ".join(parts) + "."
    else:
        interpretation = "No features are statistically significant predictors."

    return {
        "model_type": "logistic",
        "coefficients": coefficients,
        "pseudo_r2": round(pseudo_r2, 4),
        "vif": vif,
        "interpretation": interpretation,
    }


def ols_regression(df, target: str, features: list[str]) -> dict:
    """OLS regression with HC1 robust standard errors.

    Args:
        df: pandas DataFrame
        target: continuous target column name
        features: list of feature column names

    Returns:
        dict with coefficients, r2, interpretation.
    """
    import statsmodels.api as sm

    X = df[features].values.astype(float)
    y = df[target].values.astype(float)
    X_sm = sm.add_constant(X)

    model = sm.OLS(y, X_sm).fit(cov_type="HC1")

    coefficients = {}
    for i, feat in enumerate(features):
        idx = i + 1  # skip constant
        coefficients[feat] = {
            "beta": round(float(model.params[idx]), 4),
            "se": round(float(model.bse[idx]), 4),
            "p_value": round(float(model.pvalues[idx]), 6),
            "significant": float(model.pvalues[idx]) < 0.05,
        }

    return {
        "model_type": "ols",
        "coefficients": coefficients,
        "r2": round(float(model.rsquared), 4),
        "adj_r2": round(float(model.rsquared_adj), 4),
        "interpretation": model.summary().as_text()[:500],
    }


# ============================================================
#  TIME SERIES (/ds:timeseries) — STAT 153
# ============================================================

def acf_analysis(series: list, max_lag: int = 20) -> dict:
    """ACF/PACF analysis on a binary or numeric series.

    Args:
        series: list of outcomes (1/0 for win/loss, or float returns)
        max_lag: maximum lag to compute

    Returns:
        dict with acf values, significant lags, interpretation.
    """
    import numpy as np
    from statsmodels.tsa.stattools import acf as sm_acf

    arr = np.array(series, dtype=float)
    if len(arr) < max_lag + 1:
        max_lag = max(1, len(arr) - 2)

    if len(arr) < 3:
        return {
            "acf": {},
            "significant_lags": [],
            "interpretation": "Insufficient data for ACF analysis.",
        }

    acf_vals, confint = sm_acf(arr, nlags=max_lag, alpha=0.05)

    # Significant lags: where ACF is outside 95% CI around zero
    significant = []
    acf_dict = {}
    for i in range(1, len(acf_vals)):
        acf_dict[f"lag_{i}"] = round(float(acf_vals[i]), 4)
        # CI is around the ACF value, check if 0 is outside
        if confint[i][0] > 0 or confint[i][1] < 0:
            significant.append(i)

    if acf_vals[1] > 0.3:
        interp = "Positive autocorrelation at lag 1: wins tend to follow wins (streaky/momentum)."
    elif acf_vals[1] < -0.3:
        interp = "Negative autocorrelation at lag 1: wins tend to follow losses (mean reversion)."
    else:
        interp = "Outcomes appear approximately independent (no significant autocorrelation)."

    return {
        "acf": acf_dict,
        "significant_lags": significant,
        "interpretation": interp,
    }


def adf_stationarity(series: list) -> dict:
    """Augmented Dickey-Fuller test for stationarity.

    Args:
        series: numeric series (e.g., rolling WR, cumulative returns)

    Returns:
        dict with adf_stat, p_value, stationary verdict.
    """
    import numpy as np
    from statsmodels.tsa.stattools import adfuller

    arr = np.array(series, dtype=float)
    if len(arr) < 10:
        return {
            "adf_stat": 0.0,
            "p_value": 1.0,
            "stationary": False,
            "interpretation": "Insufficient data for stationarity test (need >= 10 observations).",
        }

    result = adfuller(arr, autolag="AIC")
    adf_stat, p_value = result[0], result[1]
    stationary = p_value < 0.05

    interp = (
        f"Series is {'stationary' if stationary else 'non-stationary'} "
        f"(ADF={adf_stat:.2f}, p={p_value:.4f}). "
        f"{'Edge appears stable over time.' if stationary else 'Edge may be decaying or regime-dependent.'}"
    )

    return {
        "adf_stat": round(float(adf_stat), 4),
        "p_value": round(float(p_value), 6),
        "stationary": stationary,
        "interpretation": interp,
    }


def rolling_wr_regime(wins_losses: list, window: int = 30) -> dict:
    """Detect regimes from rolling win rate.

    Args:
        wins_losses: list of 1 (win) and 0 (loss)
        window: rolling window size

    Returns:
        dict with n_regimes, current regime, regime durations.
    """
    import numpy as np

    arr = np.array(wins_losses, dtype=float)
    if len(arr) < window:
        return {
            "n_regimes": 0,
            "current_regime": "unknown",
            "regime_durations": [],
            "rolling_wr_current": float(arr.mean()) if len(arr) > 0 else 0.0,
            "interpretation": f"Insufficient data for regime detection (need >= {window} trades).",
        }

    rolling = np.convolve(arr, np.ones(window) / window, mode="valid")
    breakeven = 0.5

    # Identify regime transitions
    above = rolling > breakeven
    transitions = np.diff(above.astype(int))
    n_transitions = int(np.sum(np.abs(transitions)))
    n_regimes = n_transitions + 1

    # Regime durations
    durations = []
    current_start = 0
    for i in range(len(transitions)):
        if transitions[i] != 0:
            durations.append(i - current_start + 1)
            current_start = i + 1
    durations.append(len(transitions) - current_start + 1)

    current_wr = float(rolling[-1])
    current_regime = "positive" if current_wr > breakeven else "negative"

    return {
        "n_regimes": n_regimes,
        "current_regime": current_regime,
        "regime_durations": durations,
        "rolling_wr_current": round(current_wr, 4),
        "interpretation": (
            f"{n_regimes} regime(s) detected. Currently in {current_regime} regime "
            f"(rolling WR={current_wr * 100:.1f}%)."
        ),
    }


# ============================================================
#  CLASSIFICATION (/ds:classify) — DATA 100/144
# ============================================================

def decision_tree_classify(df, target: str, features: list[str], max_depth: int = 4) -> dict:
    """Decision tree classification with cross-validation.

    Args:
        df: pandas DataFrame
        target: binary target column
        features: feature columns
        max_depth: max tree depth for interpretability

    Returns:
        dict with accuracy, cv scores, feature importance, decision rules.
    """
    import numpy as np
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.model_selection import cross_val_score

    X = df[features].values.astype(float)
    y = df[target].values.astype(float)

    dt = DecisionTreeClassifier(max_depth=max_depth, random_state=42)
    dt.fit(X, y)

    accuracy = float(dt.score(X, y))

    n_splits = min(5, max(2, len(y) // 10))
    if n_splits >= 2 and len(y) >= 10:
        cv_scores = cross_val_score(dt, X, y, cv=n_splits, scoring="accuracy")
        cv_mean = float(cv_scores.mean())
        cv_std = float(cv_scores.std())
    else:
        cv_mean = accuracy
        cv_std = 0.0

    # Feature importance (Gini-based from tree)
    importances = dt.feature_importances_
    feature_imp = [
        {"feature": feat, "importance": round(float(imp), 4)}
        for feat, imp in sorted(zip(features, importances), key=lambda x: -x[1])
    ]

    # Decision rules as text
    try:
        rules_text = export_text(dt, feature_names=features, max_depth=3)
    except Exception:
        rules_text = "Rules unavailable"

    return {
        "accuracy": round(accuracy, 4),
        "cv_accuracy": {"mean": round(cv_mean, 4), "std": round(cv_std, 4)},
        "feature_importance": feature_imp,
        "decision_rules": rules_text[:1000],
    }


def permutation_importance_analysis(model, X, y, feature_names: list[str], n_repeats: int = 10) -> dict:
    """Permutation importance for any fitted model.

    Args:
        model: fitted sklearn model
        X: feature array
        y: target array
        feature_names: list of feature names
        n_repeats: number of permutation repeats

    Returns:
        dict with feature importance ranking.
    """
    from sklearn.inspection import permutation_importance as sk_perm

    result = sk_perm(model, X, y, n_repeats=n_repeats, random_state=42)

    importances = [
        {
            "feature": feat,
            "importance": round(float(result.importances_mean[i]), 4),
            "std": round(float(result.importances_std[i]), 4),
        }
        for i, feat in enumerate(feature_names)
    ]
    importances.sort(key=lambda x: -x["importance"])

    return {"permutation_importance": importances}


# ============================================================
#  VALIDATION (/ds:validate) — DATA 100 + trading
# ============================================================

def walk_forward_cv(returns: list[float], n_splits: int = 5) -> dict:
    """Walk-forward cross-validation respecting temporal ordering.

    Args:
        returns: per-trade dollar returns (positive = win, negative = loss)
        n_splits: number of sequential splits

    Returns:
        dict with split results, mean test WR, consistency assessment.
    """
    n = len(returns)
    if n < n_splits * 2:
        return {
            "split_results": [],
            "mean_test_wr": 0.0,
            "consistent": False,
            "splits_positive": 0,
            "interpretation": f"Insufficient data for {n_splits}-fold walk-forward (need >= {n_splits * 2} trades).",
        }

    split_size = n // (n_splits + 1)
    results = []

    for i in range(n_splits):
        train_end = split_size * (i + 1)
        test_start = train_end
        test_end = min(test_start + split_size, n)

        if test_end <= test_start:
            break

        train = returns[:train_end]
        test = returns[test_start:test_end]

        train_wr = sum(1 for r in train if r > 0) / len(train) if train else 0
        test_wr = sum(1 for r in test if r > 0) / len(test) if test else 0

        results.append({
            "split": i + 1,
            "train_n": len(train),
            "test_n": len(test),
            "train_wr": round(train_wr, 4),
            "test_wr": round(test_wr, 4),
        })

    test_wrs = [r["test_wr"] for r in results]
    mean_test_wr = sum(test_wrs) / len(test_wrs) if test_wrs else 0
    splits_positive = sum(1 for w in test_wrs if w > 0.5)
    consistent = splits_positive >= len(test_wrs) * 0.6  # Majority positive

    return {
        "split_results": results,
        "mean_test_wr": round(mean_test_wr, 4),
        "consistent": consistent,
        "splits_positive": splits_positive,
        "interpretation": (
            f"Walk-forward: {splits_positive}/{len(results)} splits positive. "
            f"Mean test WR={mean_test_wr * 100:.1f}%. "
            f"{'Consistent edge.' if consistent else 'Inconsistent - edge may be unstable.'}"
        ),
    }


def deflated_sharpe(returns: list[float], k: int = 3) -> dict:
    """Deflated Sharpe Ratio adjusted for multiple testing.

    Args:
        returns: per-trade dollar returns
        k: number of strategy parameters tested (for multiple testing adjustment)

    Returns:
        dict with sharpe, DSR, overfit risk assessment.
    """
    from scipy import stats as sp_stats
    import numpy as np

    arr = np.array(returns, dtype=float)
    n = len(arr)
    if n < 5:
        return {
            "sharpe_hat": 0.0,
            "dsr_value": 0.0,
            "overfit_risk": "UNKNOWN",
            "interpretation": "Insufficient data for DSR (need >= 5 trades).",
        }

    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1))
    if std_r == 0:
        return {
            "sharpe_hat": 0.0 if mean_r == 0 else float("inf"),
            "dsr_value": 0.0,
            "overfit_risk": "HIGH",
            "interpretation": "Zero variance in returns - likely constant outcome.",
        }

    sr_hat = mean_r / std_r
    skew = float(sp_stats.skew(arr))
    kurt = float(sp_stats.kurtosis(arr, fisher=True))

    # Expected max Sharpe under null (multiple testing)
    euler_mascheroni = 0.5772
    if k > 1:
        e_max_sr = math.sqrt(2 * math.log(k)) - (
            (euler_mascheroni + math.log(math.pi)) / (2 * math.sqrt(2 * math.log(k)))
        )
    else:
        e_max_sr = 0.0

    # DSR: probability that observed SR exceeds expected max under null
    sr_var = (1 - skew * sr_hat + (kurt / 4) * sr_hat ** 2) / n
    if sr_var <= 0:
        sr_var = 1 / n

    sr_se = math.sqrt(sr_var)
    if sr_se > 0:
        dsr = float(sp_stats.norm.cdf((sr_hat - e_max_sr) / sr_se))
    else:
        dsr = 0.0

    if dsr > 0.95:
        risk = "LOW"
    elif dsr > 0.5:
        risk = "MODERATE"
    else:
        risk = "HIGH"

    return {
        "sharpe_hat": round(sr_hat, 4),
        "dsr_value": round(dsr, 4),
        "expected_max_sharpe": round(e_max_sr, 4),
        "k_params": k,
        "overfit_risk": risk,
        "interpretation": (
            f"Sharpe={sr_hat:.2f}, DSR={dsr:.2f} (k={k} params tested). "
            f"Overfit risk: {risk}."
        ),
    }


def regime_stability_check(wr_by_regime: dict, min_n: int = 15) -> dict:
    """Check if edge is stable across multiple regimes.

    Args:
        wr_by_regime: dict mapping regime_name -> {"wr": float, "n": int}
        min_n: minimum trades per regime for PRELIMINARY confidence

    Returns:
        dict with stability assessment.
    """
    results = {}
    positive_count = 0
    total_regimes = len(wr_by_regime)

    for regime, data in wr_by_regime.items():
        wr = data.get("wr", 0)
        n = data.get("n", 0)
        r8 = _r8_label(n)
        positive = wr > 0.5 and n >= 1
        if positive:
            positive_count += 1
        results[regime] = {
            "wr": round(wr, 4),
            "n": n,
            "r8_label": r8,
            "positive": positive,
            "sufficient_data": n >= min_n,
        }

    stable = positive_count >= 2 and positive_count >= total_regimes * 0.5
    weakest = min(results.items(), key=lambda x: x[1]["wr"]) if results else ("none", {"wr": 0, "n": 0})

    return {
        "positive_regimes": positive_count,
        "total_regimes": total_regimes,
        "stable": stable,
        "regime_results": results,
        "weakest_regime": f"{weakest[0]} (WR={weakest[1]['wr'] * 100:.1f}%, n={weakest[1]['n']}, {_r8_label(weakest[1]['n'])})",
        "interpretation": (
            f"{positive_count}/{total_regimes} regimes positive. "
            f"{'Stable across regimes.' if stable else 'Regime-dependent - edge may not generalize.'}"
        ),
    }


# ============================================================
#  OPTIMIZATION (/ds:optimize) — ECON 100A / Math 53
# ============================================================

def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> dict:
    """Kelly criterion for optimal position sizing.

    Args:
        win_rate: probability of winning (0-1)
        avg_win: average dollar win per trade
        avg_loss: average dollar loss per trade (positive number)

    Returns:
        dict with full Kelly, half Kelly, confidence-adjusted.
    """
    if avg_loss == 0:
        return {
            "full_kelly": 0.0,
            "half_kelly": 0.0,
            "confidence_adjusted": 0.0,
            "recommended_bet_pct": 0.0,
            "interpretation": "Cannot compute Kelly: avg_loss is zero.",
        }

    b = avg_win / avg_loss  # odds ratio
    p = win_rate
    q = 1 - p

    f_star = (p * b - q) / b
    f_star = max(0.0, f_star)  # Never negative (means don't bet)

    half_kelly = f_star / 2

    return {
        "full_kelly": round(f_star, 4),
        "half_kelly": round(half_kelly, 4),
        "confidence_adjusted": round(f_star * 0.9, 4),  # 10% haircut
        "recommended_bet_pct": round(half_kelly, 4),
        "odds_ratio": round(b, 4),
        "interpretation": (
            f"Kelly fraction: {f_star * 100:.1f}% (WR={p * 100:.1f}%, odds={b:.2f}). "
            f"Half-Kelly recommended: {half_kelly * 100:.1f}%. "
            f"{'No edge - do not bet.' if f_star == 0 else ''}"
        ),
    }


def grid_search_params(
    param_grid: dict,
    returns_by_params: dict,
) -> dict:
    """Simple grid search over pre-computed param -> returns mappings.

    Args:
        param_grid: dict of param_name -> list of values
        returns_by_params: dict mapping param_tuple_str -> list of returns

    Returns:
        dict with best params and sensitivity analysis.
    """
    import numpy as np

    best_sharpe = -float("inf")
    best_key = None

    results = {}
    for key, rets in returns_by_params.items():
        arr = np.array(rets, dtype=float)
        if len(arr) < 2:
            continue
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr, ddof=1))
        sharpe = mean_r / std_r if std_r > 0 else 0.0
        results[key] = {"sharpe": round(sharpe, 4), "n": len(arr), "mean_return": round(mean_r, 4)}
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_key = key

    return {
        "best_params": best_key,
        "best_sharpe": round(best_sharpe, 4),
        "all_results": results,
    }


# ============================================================
#  HELPERS
# ============================================================

def _r8_label(n: int) -> str:
    """R8 sample-size label."""
    if n < 15:
        return "ANECDOTAL"
    elif n < 30:
        return "PRELIMINARY"
    elif n < 100:
        return "MODERATE"
    else:
        return "SUBSTANTIAL"
