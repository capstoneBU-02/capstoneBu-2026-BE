from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수(.env)에서 읽어오는 설정. Spring의 application.yml 역할."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://capstone:capstone@localhost:5432/capstone"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:8333"
    s3_public_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "capstone"
    s3_secret_key: str = "capstone-secret"
    s3_bucket: str = "capstone"
    s3_region: str = "us-east-1"
    s3_presigned_expires_seconds: int = 900

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
