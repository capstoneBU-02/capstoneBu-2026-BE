"""저장소 key 규칙. key는 이 함수로만 만든다."""

from app.domains.processing.models import Stem


def input_key(stem: Stem) -> str:
    """preprocess 결과: 앞뒤 여유를 포함한 구간 WAV."""
    return f"{stem.workspace_id}/{stem.track_id}/segments/{stem.segment_id}/{stem.id}/input.wav"


def guitar_key(stem: Stem) -> str:
    """separate 결과: 구간 범위로 자른 기타 음원 (TAB·CMP 입력)."""
    return part_key(stem, "guitar")


def part_key(stem: Stem, part: str) -> str:
    """separate 결과: 구간 범위로 자른 악기별 음원과 반주 (예: bass.wav, backing.wav)."""
    if not part.isalpha() or not part.islower():
        raise ValueError(f"악기 이름이 올바르지 않습니다: {part!r}")
    return f"{stem.workspace_id}/{stem.track_id}/stems/{stem.segment_id}/{stem.id}/{part}.wav"
