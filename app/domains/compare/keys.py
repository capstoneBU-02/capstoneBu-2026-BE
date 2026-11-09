"""저장소 key 규칙. key는 이 함수로만 만든다."""

from app.domains.compare.models import Comparison


def _base(c: Comparison) -> str:
    """비교 결과 폴더."""
    return f"{c.workspace_id}/{c.track_id}/comparisons/{c.id}"


def alignment_key(c: Comparison) -> str:
    """align 결과 (contract.AlignmentResult JSON)."""
    return f"{_base(c)}/alignment.json"


def notes_key(c: Comparison) -> str:
    """transcription 결과 (contract.NotesResult JSON)."""
    return f"{_base(c)}/notes.json"


def result_key(c: Comparison) -> str:
    """최종 결과 (전체 빠르기·요약·기준). 구간 목록은 comparison_regions에 있다."""
    return f"{_base(c)}/result.json"
