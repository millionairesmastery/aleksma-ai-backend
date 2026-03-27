"""Centralized application configuration.

Reads from environment variables with sensible defaults for local development.
In production, set these via Railway/Render environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Settings:
    """Application settings — loaded from environment variables."""

    # Database
    database_url: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 10

    # Auth
    jwt_secret: str = "dev-secret-change-in-production"

    # AI
    anthropic_api_key: str = ""

    # Execution
    execution_backend: str = "local"  # "local" or "modal"

    # Email
    resend_api_key: str = ""

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

    # Environment
    environment: str = "development"  # "development", "staging", "production"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables."""
        cors_raw = os.environ.get("CORS_ORIGINS", "*")
        cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

        return cls(
            database_url=os.environ.get("DATABASE_URL", "postgresql://localhost:5432/ai_cad_studio"),
            db_pool_min=int(os.environ.get("DB_POOL_MIN", "1")),
            db_pool_max=int(os.environ.get("DB_POOL_MAX", "10")),
            jwt_secret=os.environ.get("JWT_SECRET", "dev-secret-change-in-production"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            execution_backend=os.environ.get("EXECUTION_BACKEND", "local"),
            resend_api_key=os.environ.get("RESEND_API_KEY", ""),
            cors_origins=cors_origins,
            environment=os.environ.get("ENVIRONMENT", "development"),
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get application settings (cached singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
