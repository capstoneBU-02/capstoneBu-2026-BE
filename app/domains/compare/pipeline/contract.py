"""판정 함수와 작업 사이의 입출력 형태. 단계 결과는 이 형태의 JSON으로 저장소에 남긴다.

시간은 모두 구간 시작 기준 밀리초 정수다.
형태를 바꾸면 tasks.py와 함께 고치고 config_version을 올린다.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any


class InvalidAudioError(Exception):
    """연주 음원을 읽을 수 없거나 무음이다 (PERFORMANCE_INVALID_AUDIO, 재시도하지 않음)."""


@dataclass(frozen=True)
class AlignmentResult:
    """align 단계 결과: 두 연주의 시간 대응과 전체 빠르기."""

    path: list[tuple[int, int]]  # (기준 ms, 연주 ms) 대응점
    slope: float  # DTW 경로 평균 기울기 (1보다 크면 연주가 느림)
    is_slow: bool  # 전체적으로 느림 (CMP-02)
    rerecord_reason: str | None = None  # 처리 전 입력 검사 결과 (예: RERECORD_TOO_SHORT)


@dataclass(frozen=True)
class Note:
    """인식한 음 하나."""

    start_ms: int
    end_ms: int
    pitch: int  # MIDI 번호
    confidence: float


@dataclass(frozen=True)
class NotesResult:
    """transcription 단계 결과: 양쪽 연주의 음."""

    reference: list[Note]
    performance: list[Note]
    rerecord_reason: str | None = None  # 예: RERECORD_FEW_NOTES


@dataclass(frozen=True)
class Region:
    """다시 들어 볼 구간 하나."""

    type: str  # RegionType
    start_ms: int
    end_ms: int
    performance_start_ms: int
    performance_end_ms: int
    reason: str | None = None  # unavailable일 때만 (LOW_CONFIDENCE / UNCLEAR_ALIGNMENT)


@dataclass(frozen=True)
class JudgeResult:
    """compare 단계 결과."""

    regions: list[Region] = field(default_factory=list)
    not_comparable_reason: str | None = None  # 예: LOW_MATCH_RATE, TEMPO_OUT_OF_RANGE


def to_json(value: Any) -> bytes:
    """dataclass를 저장용 JSON으로."""
    return json.dumps(asdict(value), ensure_ascii=False).encode()


def alignment_from_json(data: bytes) -> AlignmentResult:
    """저장된 align 결과를 읽는다."""
    raw = json.loads(data)
    raw["path"] = [tuple(p) for p in raw["path"]]
    return AlignmentResult(**raw)


def notes_from_json(data: bytes) -> NotesResult:
    """저장된 transcription 결과를 읽는다."""
    raw = json.loads(data)
    return NotesResult(
        reference=[Note(**n) for n in raw["reference"]],
        performance=[Note(**n) for n in raw["performance"]],
        rerecord_reason=raw.get("rerecord_reason"),
    )
