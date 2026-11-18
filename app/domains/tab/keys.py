"""저장소 key 규칙. key는 이 함수로만 만든다."""

from app.domains.tab.models import Tab


def _base(tab: Tab) -> str:
    return f"{tab.workspace_id}/{tab.track_id}/tabs/{tab.id}"


def notes_key(tab: Tab) -> str:
    """transcription 결과: 음 인식 노트 (초 단위)."""
    return f"{_base(tab)}/notes.json"


def rhythm_key(tab: Tab) -> str:
    """rhythm 결과: 박 위치·길이가 정해진 노트와 템포."""
    return f"{_base(tab)}/rhythm.json"


def tab_json_key(tab: Tab) -> str:
    """tab 결과: 줄·프렛까지 정해진 최종 노트 (결과 조회 API가 읽는다)."""
    return f"{_base(tab)}/tab.json"


def musicxml_key(tab: Tab) -> str:
    """tab 결과: 렌더링·다운로드용 악보."""
    return f"{_base(tab)}/tab.musicxml"
