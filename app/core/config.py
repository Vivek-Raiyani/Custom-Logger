from functools import lru_cache
from pathlib import Path
from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    STAGING = "staging"
    TEST = "test"

    

class Settings(BaseSettings):
    # App
    app_name: str = "Lycan"
    app_version: str = "1.0.0"
    environment: Environment = Environment.DEVELOPMENT

    # Database
    db_name: str = "lycan"
    database_url: str = "sqlite+aiosqlite:///./lycan.db"  # override via env

    # CORS
    allow_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    # Auth / JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Logs
    log_file_path: str = "logs/app.log"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()