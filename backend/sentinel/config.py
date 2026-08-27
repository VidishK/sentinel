import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve().parent
if (_HERE.parent / "frontend").exists() or (_HERE.parent / "certs").exists():
    _ROOT = _HERE.parent
elif (_HERE.parents[1] / "frontend").exists():
    _ROOT = _HERE.parents[1]
else:
    _ROOT = _HERE.parent
_ENV_FILE = _ROOT / ".env"


def _bundled_sslrootcert() -> str | None:
    candidates = [
        os.environ.get("SSLROOTCERT"),
        str(_ROOT / "certs" / "cc-ca.crt"),
        str(_HERE.parent / "certs" / "cc-ca.crt"),
        "/app/certs/cc-ca.crt",
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _normalize_database_url(url: str, *, inject_default_cert: bool = True) -> str:
    """Rewrite sslrootcert so the file exists.

    Control-plane Cockroach URLs inject the bundled Cloud CA. User-provided
    Postgres URLs must not — that CA will fail against Neon, RDS, etc.
    """
    parts = urlsplit(url)
    qs = dict(parse_qsl(parts.query, keep_blank_values=True))
    existing = qs.get("sslrootcert")
    bundled = _bundled_sslrootcert()

    if inject_default_cert:
        cert = next(
            (
                path
                for path in (os.environ.get("SSLROOTCERT"), existing, bundled)
                if path and Path(path).is_file()
            ),
            None,
        )
        if cert:
            qs["sslrootcert"] = cert
        elif existing:
            qs.pop("sslrootcert", None)
            if qs.get("sslmode") == "verify-full":
                qs["sslmode"] = "require"
    elif existing:
        path = Path(existing)
        is_bundled = path.name == "cc-ca.crt"
        if not is_bundled and bundled and path.is_file():
            try:
                is_bundled = path.resolve().samefile(Path(bundled))
            except OSError:
                is_bundled = False
        if not path.is_file() or is_bundled:
            qs.pop("sslrootcert", None)
            if qs.get("sslmode") == "verify-full":
                qs["sslmode"] = "require"

    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )


def normalize_source_dsn(engine: str, dsn: str) -> str:
    """Normalize a user-connected DSN without poisoning Postgres with our CA."""
    engine = engine.lower().strip()
    if engine == "cockroach":
        return _normalize_database_url(dsn, inject_default_cert=True)
    if engine == "postgres":
        return _normalize_database_url(dsn, inject_default_cert=False)
    return dsn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    ccloud_cluster_name: str = "sentinel"

    aws_region: str = "us-east-1"
    bedrock_model_id: str = "amazon.nova-pro-v1:0"
    bedrock_embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dim: int = 1024

    artifact_bucket: str = ""

    risk_rows_high: int = 1000
    risk_rows_medium: int = 100

    google_client_id: str = ""
    auth_from_email: str = "Sentinel <onboarding@resend.dev>"
    auth_public_url: str = "http://127.0.0.1:5173"
    auth_dev_mode: bool = True
    resend_api_key: str = ""

    @field_validator("database_url")
    @classmethod
    def rewrite_ssl_cert(cls, value: str) -> str:
        return _normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
