from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root is two levels above this file: backend/sentinel/config.py -> repo root
_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    ccloud_cluster_name: str = "sentinel"

    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    bedrock_embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dim: int = 1024

    artifact_bucket: str = ""

    risk_rows_high: int = 1000
    risk_rows_medium: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
