"""리듬 정량화. 박·마디 첫 박을 찾고, 음 인식 결과를 박 단위 격자에 맞춘다 (AI 설계서 8장).

박 추정은 드럼이 남아 있는 분리 전 구간 음원으로 한다. 기타 음원에는 박이 약하기 때문이다.
시간은 구간 시작 기준 초, 박 위치는 첫 마디 첫 박을 0으로 하는 박 단위다.
AI 라이브러리는 api·CI에 없으므로 함수 안에서 import한다.
"""

import logging
import statistics
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.domains.tab.schemas import Note, QuantizedNote

logger = logging.getLogger(__name__)

BEATS_PER_BAR = 4  # PoC 화면은 4/4로 표시한다
SIXTEENTH = 0.25
TRIPLET = 1 / 3  # 셋잇단 8분음표
TOLERANCE = 1e-9


class RhythmError(Exception):
    """박을 찾지 못했다 (RHYTHM_FAILED)."""


@dataclass(frozen=True)
class BeatGrid:
    """박과 마디 첫 박 시각(초, 구간 시작 기준)."""

    beats: tuple[float, ...]
    downbeats: tuple[float, ...] = ()


@dataclass(frozen=True)
class RhythmResult:
    notes: list[QuantizedNote]
    tempo_bpm: float
    beats_per_bar: int | None  # 마디 첫 박 간격으로 추정한 박자. 알 수 없으면 None
    non_4_4: bool


def quantize(notes: Sequence[Note], grid: BeatGrid) -> RhythmResult:
    """음의 시작·길이를 16분음표 또는 셋잇단 격자에 맞춘다. 입력 순서를 유지한다."""
    beats = list(grid.beats)
    if len(beats) < 2:
        raise RhythmError("박이 두 개 미만입니다")

    origin = _origin(notes, grid, beats)
    starts = [_snap(_position(note.start, beats) - origin) for note in notes]
    onsets = sorted(set(starts))
    quantized = [
        QuantizedNote(
            **note.model_dump(),
            beat=start,
            duration=_duration(start, _position(note.end, beats) - origin, onsets),
        )
        for note, start in zip(notes, starts, strict=True)
    ]

    beats_per_bar = _beats_per_bar(grid.downbeats, beats)
    return RhythmResult(
        notes=quantized,
        tempo_bpm=60.0 / statistics.median(b - a for a, b in zip(beats, beats[1:], strict=False)),
        beats_per_bar=beats_per_bar,
        non_4_4=beats_per_bar is not None and beats_per_bar != BEATS_PER_BAR,
    )


def _position(time: float, beats: Sequence[float]) -> float:
    """시각을 박 번호(실수)로 바꾼다. 처음·마지막 박 밖은 가장 가까운 박 간격으로 늘린다."""
    i = bisect_right(beats, time) - 1
    if i < 0:
        return (time - beats[0]) / (beats[1] - beats[0])
    if i >= len(beats) - 1:
        return len(beats) - 1 + (time - beats[-1]) / (beats[-1] - beats[-2])
    return i + (time - beats[i]) / (beats[i + 1] - beats[i])


def _origin(notes: Sequence[Note], grid: BeatGrid, beats: Sequence[float]) -> float:
    """박 0의 위치. 첫 마디 첫 박에 두고, 그보다 앞선 음이 있으면 한 마디씩 앞당긴다."""
    origin = float(round(_position(grid.downbeats[0], beats))) if grid.downbeats else 0.0
    earliest = min((_position(note.start, beats) for note in notes), default=origin)
    while _snap(earliest - origin) < 0:
        origin -= BEATS_PER_BAR
    return origin


def _snap(position: float) -> float:
    """16분음표와 셋잇단 격자 중 오차가 작은 쪽에 맞춘다. 같으면 16분음표."""
    sixteenth = round(position / SIXTEENTH) * SIXTEENTH
    triplet = round(position / TRIPLET) * TRIPLET
    if abs(position - triplet) + TOLERANCE < abs(position - sixteenth):
        return triplet
    return sixteenth


def _duration(start: float, end: float, onsets: Sequence[float]) -> float:
    """다음 음 시작과 원래 끝 중 빠른 쪽까지. 너무 짧으면 격자 한 칸."""
    later = [onset for onset in onsets if onset > start + TOLERANCE]
    stop = min(_snap(end), later[0]) if later else _snap(end)
    unit = SIXTEENTH if abs(start / SIXTEENTH - round(start / SIXTEENTH)) < TOLERANCE else TRIPLET
    return max(stop - start, unit)


def _beats_per_bar(downbeats: Sequence[float], beats: Sequence[float]) -> int | None:
    if len(downbeats) < 2:
        return None
    positions = [_position(time, beats) for time in downbeats]
    gaps = [round(b - a) for a, b in zip(positions, positions[1:], strict=False)]
    return int(statistics.median(gaps))


def estimate_beats(
    audio_path: str, offset_ms: int, duration_ms: int, settings: Settings
) -> BeatGrid:
    """구간 음원에서 박을 찾는다. offset_ms는 앞쪽 여유(stems.input_pad_ms)다.

    Beat This!를 쓸 수 없으면 librosa beat_track으로 대신하고, 마디 첫 박은 4박마다로 둔다.
    """
    import soundfile

    sample_rate = soundfile.info(audio_path).samplerate
    signal, _ = soundfile.read(
        audio_path,
        start=round(offset_ms * sample_rate / 1000),
        frames=round(duration_ms * sample_rate / 1000),
        dtype="float32",
        always_2d=True,
    )
    mono = signal.mean(axis=1)
    try:
        beats, downbeats = _load_beat_model(_checkpoint(settings))(mono, sample_rate)
    except Exception:
        logger.warning("Beat This! 박 추정 실패, librosa로 대신합니다", exc_info=True)
        beats, downbeats = _librosa_beats(mono, sample_rate)

    end = duration_ms / 1000
    return BeatGrid(
        beats=tuple(float(t) for t in beats if 0 <= t <= end),
        downbeats=tuple(float(t) for t in downbeats if 0 <= t <= end),
    )


def _checkpoint(settings: Settings) -> str:
    """scripts/download_models.py가 받은 파일이 있으면 그것을, 없으면 이름으로 읽는다."""
    path = (
        Path(settings.ai_model_cache_dir) / "beat_this" / f"beat_this-{settings.ai_beat_model}.ckpt"
    )
    return str(path) if path.exists() else settings.ai_beat_model


@lru_cache(maxsize=2)
def _load_beat_model(checkpoint: str) -> Any:
    from beat_this.inference import Audio2Beats

    return Audio2Beats(checkpoint_path=checkpoint, device="cpu")


def _librosa_beats(signal: Any, sample_rate: int) -> tuple[list[float], list[float]]:
    import librosa

    _, beats = librosa.beat.beat_track(y=signal, sr=sample_rate, units="time")
    times = [float(t) for t in beats]
    return times, times[::BEATS_PER_BAR]
