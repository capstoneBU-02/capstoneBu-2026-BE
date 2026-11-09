"""정렬·음 인식·판정 함수 자리 (복재환 구현 예정, 11/2 합의).

작업 쪽은 아래 시그니처만 가정한다. 구현 전에는 NotImplementedError가 나서 작업이
AI_WORKER_ERROR(재시도 가능)로 끝난다. 기준값은 get_settings()의 AI 설정으로 읽는다.
"""

from app.core.config import Settings
from app.domains.compare.pipeline.contract import AlignmentResult, JudgeResult, NotesResult


def align(ref_path: str, perf_path: str, settings: Settings) -> AlignmentResult:
    """크로마·onset + DTW로 두 연주의 시간을 맞추고 전체 빠르기를 판정한다.

    처리 전 입력 검사(길이가 구간의 절반 미만·2배 초과, 소리 크기)에 걸리면
    rerecord_reason을 채운다.
    """
    raise NotImplementedError("align: 복재환 구현 예정")


def transcribe_both(ref_path: str, perf_path: str, settings: Settings) -> NotesResult:
    """양쪽 연주의 음을 Basic Pitch로 인식한다.

    인식 음 수가 기준의 절반 미만이면 rerecord_reason을 채운다.
    """
    raise NotImplementedError("transcribe_both: 복재환 구현 예정")


def judge(alignment: AlignmentResult, notes: NotesResult, settings: Settings) -> JudgeResult:
    """음·타이밍 차이를 판정하고 판단 보류 구간을 나눈다. 비교할 수 없으면 not_comparable_reason."""
    raise NotImplementedError("judge: 복재환 구현 예정")
