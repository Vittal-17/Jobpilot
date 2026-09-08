import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

_env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))

class Settings(BaseSettings):
    postgres_user: str = "jobpilot"
    postgres_password: str = "jobpilot_pass"
    postgres_db: str = "jobpilot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str | None = None

    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_daily_limit: int = 10

    jooble_api_key: str = ""
    jooble_daily_limit: int = 1
    jooble_lifetime_limit: int = 500

    environment: str = "development"

    api_secret_key: str

    @field_validator('api_secret_key')
    @classmethod
    def validate_api_secret_key(cls, v: str, info) -> str:
        env = info.data.get('environment', 'development')
        if env == "production":
            if not v or not v.strip():
                raise ValueError("API_SECRET_KEY cannot be empty or whitespace in production.")
            if len(v) < 16:
                raise ValueError("API_SECRET_KEY must be at least 16 characters long in production.")
            unsafe_keys = {"your_strong_internal_api_secret_key_here", "dev_secret_key_1234567"}
            if v in unsafe_keys:
                raise ValueError("API_SECRET_KEY must not be a known unsafe default in production.")
        return v

    model_config = SettingsConfigDict(env_file=_env_path, env_file_encoding="utf-8", extra="ignore")

    def get_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

settings = Settings()
