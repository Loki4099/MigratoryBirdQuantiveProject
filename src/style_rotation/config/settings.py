from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STYLE_ROTATION_",
        extra="ignore",
        frozen=True,
    )

    environment: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://style_rotation:style_rotation@localhost:5432/style_rotation"
    )
    log_level: str = "INFO"
    system_version: str = "0.2.0"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    yahoo_timeout_seconds: float = 30.0
    fred_csv_url: str = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
