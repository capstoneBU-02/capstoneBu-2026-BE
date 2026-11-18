from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수(.env)에서 읽어오는 설정. Spring의 application.yml 역할."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"
    cookie_secure: bool = False

    workspace_max_storage_bytes: int = 500 * 1024 * 1024  # 500MB
    workspace_max_tracks: int = 50
    upload_max_bytes: int = 50 * 1024 * 1024  # 50MB
    track_max_duration_ms: int = 300_000  # 5분

    database_url: str = "postgresql+psycopg://capstone:capstone@localhost:5432/capstone"
    redis_url: str = "redis://localhost:6379/0"
    # 작업 등록 시 Redis 연결 제한. 끊겨 있으면 약 (재시도 + 1) × 연결 제한 초 안에
    # 503 JOB_ENQUEUE_FAILED로 끝낸다 (기본값 약 10초, 연결 거부면 더 빠름)
    celery_connect_timeout_sec: float = 3.0
    celery_publish_max_retries: int = 2

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
    # 음 인식 후처리 (basic-pitch). 주파수 범위는 표준 튜닝 최저음 근처부터 1현 24프렛 근처까지
    ai_min_note_length_ms: float = 58.0
    ai_min_frequency_hz: float = 80.0
    ai_max_frequency_hz: float = 1320.0
    ai_note_merge_gap_ms: int = 30  # 같은 음높이가 이 간격 이내로 끊겼다 이어지면 합친다
    ai_separation_rms_db: float = 30.0
    # 타브 운지 Viterbi 비용 가중치. env는 JSON: AI_TAB_WEIGHTS='{"span": 1.0, ...}'
    ai_tab_weights: dict[str, float] = {"span": 1.0, "pos": 0.1, "move": 0.5, "strskip": 0.3}
    ai_tab_max_fret: int = 22
    ai_tab_span_limit: int = 4  # 이 프렛 범위를 넘는 만큼 span 벌점을 더한다
    ai_tab_span_penalty: float = 100.0
    ai_tab_max_states: int = 50  # 시점당 남기는 손 모양 후보 수 (비용 낮은 순)
    # 연주 가능성 검사: 한 시점의 프렛 범위 상한, 16분음표 안의 손 위치 이동 한계
    ai_playable_max_span: int = 5
    ai_playable_max_move: int = 7
    ai_model_cache_dir: str = "/models"

    # 분리·처리(JOB). 제한 시간은 D12 확정 전 초기값
    ai_preprocess_timeout_sec: int = 60
    ai_separation_timeout_sec: int = 600
    ai_preprocess_padding_ms: int = 500  # 구간 앞뒤 여유. 분리 후 잘라낸다
    ai_sample_rate: int = 44100
    ai_silence_dbfs: float = -50.0  # 이보다 작으면 무음
    ai_separation_min_active_ratio: float = 0.1  # 소리 나는 구간이 이 비율 미만이면 needs_review
    # 이 시간 넘게 queued로 남은 작업은 등록이 유실된 것으로 보고 실패 처리한다 (재시도 가능)
    ai_job_queued_stale_sec: int = 1800

    # 연주 비교(CMP). 제한 시간은 D12·D16 확정 전 초기값
    ai_align_timeout_sec: int = 120
    ai_compare_transcription_timeout_sec: int = 300
    ai_compare_timeout_sec: int = 60

    # 타브(TAB). 제한 시간은 D12 확정 전 초기값
    ai_tab_transcription_timeout_sec: int = 300
    ai_rhythm_timeout_sec: int = 120
    ai_tab_build_timeout_sec: int = 60
    ai_low_confidence_ratio: float = 0.5  # 이 비율 이상이 low_confidence면 needs_review

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
