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

    # AI 파이프라인 (env: AI_*). 모델 이름·임계값은 코드에 하드코딩하지 않고 여기서 읽는다.
    ai_config_version: str = "2026-10-01"  # 결과 재현용. 모델·임계값을 바꾸면 함께 올린다
    ai_separation_model: str = "BS-Roformer-SW.ckpt"
    ai_separation_fallback: str = "htdemucs_6s"
    ai_transcription_weights: str = "icassp2022-onnx"  # basic-pitch. 미세조정 시 ft-YYYYMMDD
    ai_beat_model: str = "final0"
    ai_onset_threshold: float = 0.5
    ai_frame_threshold: float = 0.3
    ai_low_confidence: float = 0.4
    ai_separation_rms_db: float = 30.0
    # 타브 운지 Viterbi 비용 가중치. env는 JSON: AI_TAB_WEIGHTS='{"span": 1.0, ...}'
    ai_tab_weights: dict[str, float] = {"span": 1.0, "pos": 0.1, "move": 0.5, "strskip": 0.3}
    ai_model_cache_dir: str = "/models"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
