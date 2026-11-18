"""단계별 제한 시간. Celery 작업과 멈춘 작업 복구(service)가 같은 값을 쓴다."""

from app.core.config import Settings
from app.domains.tab.models import TabStage

# soft 제한 시간 뒤 정리할 여유. 이 시간이 지나면 Celery가 작업을 강제로 끝낸다
HARD_LIMIT_MARGIN_SEC = 60


def soft_limit_sec(stage: TabStage, settings: Settings) -> int:
    """이 시간을 넘으면 작업 안에서 SoftTimeLimitExceeded가 난다 (AI_WORKER_TIMEOUT)."""
    if stage == TabStage.TRANSCRIPTION:
        return settings.ai_tab_transcription_timeout_sec
    if stage == TabStage.RHYTHM:
        return settings.ai_rhythm_timeout_sec
    return settings.ai_tab_build_timeout_sec


def hard_limit_sec(stage: TabStage, settings: Settings) -> int:
    """이 시간이 지나면 작업 프로세스가 끝나 있다. 그 뒤에도 processing이면 멈춘 작업이다."""
    return soft_limit_sec(stage, settings) + HARD_LIMIT_MARGIN_SEC
