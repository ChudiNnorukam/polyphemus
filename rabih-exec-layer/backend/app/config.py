import logging
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

ENV_PATH = Path(__file__).parent.parent / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


class Settings(BaseSettings):
    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    # Database
    database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Anthropic
    anthropic_api_key: str = ""

    # Dashboard auth
    api_token: str = "changeme"

    # Extraction
    drift_threshold_days: int = 3
    extraction_confidence_min: float = 0.70
    prompt_version: str = "v1.0"

    # Safety
    dry_run: bool = True

    class Config:
        env_file = str(ENV_PATH)
        case_sensitive = False
        extra = "ignore"


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if "." in name:
        logger.propagate = False
    return logger


settings = Settings()
