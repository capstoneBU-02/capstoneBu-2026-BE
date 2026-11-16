"""음 인식. Basic Pitch(ONNX)로 기타 음원의 음을 찾고 후처리한다 (AI 설계서 7장).

CMP의 transcribe_both도 이 함수를 쓴다. 시간은 구간 시작 기준 초 단위다.
AI 라이브러리는 api·CI에 없으므로 함수 안에서 import한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.domains.tab.pipeline.fingering import pitch_from_name
from app.domains.tab.schemas import Note, PlayingCondition

PUBLIC_WEIGHTS = "icassp2022-onnx"
LOWEST_MIDI_PITCH = 21  # Basic Pitch 출력의 첫 칸 (A0)
BEND_STEPS_PER_SEMITONE = 3  # Basic Pitch 벤딩 단위는 1/3 반음


@dataclass(frozen=True)
class RawNote:
    """모델이 찾은 음. 후처리 전이다."""

    start: float
    end: float
    pitch: int
    velocity: float
    confidence: float
    bend: tuple[float, ...] = ()


def transcribe(audio_path: str, condition: PlayingCondition, settings: Settings) -> list[Note]:
    """기타 음원에서 음을 찾는다. 음이 없으면 빈 목록이고, 실패 판정은 호출한 쪽이 한다."""
    return postprocess(_infer(audio_path, settings), condition, settings)


def postprocess(
    notes: Sequence[RawNote], condition: PlayingCondition, settings: Settings
) -> list[Note]:
    """최저음 밖 제거, 짧은 끊김 합치기, 동시 음 수 제한, 낮은 신뢰도 표시."""
    lowest = min(pitch_from_name(name) for name in condition.tuning) + condition.capo
    kept = [note for note in notes if note.pitch >= lowest]
    merged = _merge_short_gaps(kept, settings.ai_note_merge_gap_ms / 1000)
    limited = _limit_polyphony(merged, condition.strings)
    return [
        Note(
            start=note.start,
            end=note.end,
            pitch=note.pitch,
            velocity=note.velocity,
            confidence=note.confidence,
            bend=note.bend,
            low_confidence=note.confidence < settings.ai_low_confidence,
        )
        for note in limited
    ]


def _merge_short_gaps(notes: Sequence[RawNote], max_gap: float) -> list[RawNote]:
    """같은 음높이가 max_gap초 이내로 끊겼다 이어지면 하나로 합친다."""
    merged: list[RawNote] = []
    for note in sorted(notes, key=lambda n: (n.pitch, n.start)):
        last = merged[-1] if merged else None
        if last is not None and last.pitch == note.pitch and note.start - last.end <= max_gap:
            merged[-1] = _join(last, note)
        else:
            merged.append(note)
    return sorted(merged, key=lambda n: (n.start, n.pitch))


def _join(first: RawNote, second: RawNote) -> RawNote:
    """신뢰도는 길이로 가중 평균한다."""
    first_length = max(first.end - first.start, 0.0)
    second_length = max(second.end - second.start, 0.0)
    total = first_length + second_length
    confidence = (
        (first.confidence * first_length + second.confidence * second_length) / total
        if total > 0
        else max(first.confidence, second.confidence)
    )
    return RawNote(
        start=first.start,
        end=max(first.end, second.end),
        pitch=first.pitch,
        velocity=max(first.velocity, second.velocity),
        confidence=confidence,
        bend=first.bend + second.bend,
    )


def _limit_polyphony(notes: Sequence[RawNote], max_notes: int) -> list[RawNote]:
    """어느 순간에든 울리는 음이 max_notes개를 넘으면 신뢰도 낮은 음부터 뺀다."""
    removed: set[int] = set()
    for i, note in enumerate(notes):
        sounding = [
            j
            for j, other in enumerate(notes[: i + 1])
            if j not in removed and other.start <= note.start < other.end
        ]
        sounding.sort(key=lambda j: (notes[j].confidence, -notes[j].start))
        removed.update(sounding[: max(len(sounding) - max_notes, 0)])
    return [note for i, note in enumerate(notes) if i not in removed]


def model_path(settings: Settings) -> Path:
    """공개 가중치는 basic-pitch 패키지에 들어 있고, 미세조정 가중치는 모델 캐시에서 읽는다."""
    if settings.ai_transcription_weights == PUBLIC_WEIGHTS:
        import basic_pitch

        package = Path(basic_pitch.__file__).parent
        return package / "saved_models" / "icassp_2022" / "nmp.onnx"
    cache = Path(settings.ai_model_cache_dir)
    return cache / "basic_pitch" / f"{settings.ai_transcription_weights}.onnx"


def preload(settings: Settings) -> None:
    """워커 시작 시 모델을 한 번 읽어 둔다."""
    _load_model(str(model_path(settings)))


@lru_cache(maxsize=2)
def _load_model(path: str) -> Any:
    from basic_pitch.inference import Model

    return Model(path)


def _infer(audio_path: str, settings: Settings) -> list[RawNote]:
    from basic_pitch.inference import predict
    from basic_pitch.note_creation import model_frames_to_time

    model_output, _, events = predict(
        audio_path,
        _load_model(str(model_path(settings))),
        onset_threshold=settings.ai_onset_threshold,
        frame_threshold=settings.ai_frame_threshold,
        minimum_note_length=settings.ai_min_note_length_ms,
        minimum_frequency=settings.ai_min_frequency_hz,
        maximum_frequency=settings.ai_max_frequency_hz,
    )
    frames = model_output["note"]
    times = model_frames_to_time(frames.shape[0])
    return [_to_raw_note(event, frames, times) for event in events]


def _to_raw_note(event: Any, frames: Any, times: Any) -> RawNote:
    """음이 울리는 동안의 note 확률 평균을 신뢰도로 쓴다 (Basic Pitch는 음별 신뢰도를 주지 않음)."""
    import numpy as np

    start, end, pitch, amplitude, bends = event
    first = int(np.searchsorted(times, start, side="left"))
    last = max(int(np.searchsorted(times, end, side="right")), first + 1)
    confidence = float(np.mean(frames[first:last, int(pitch) - LOWEST_MIDI_PITCH]))
    return RawNote(
        start=max(float(start), 0.0),
        end=max(float(end), float(start), 0.0),
        pitch=int(pitch),
        velocity=min(max(float(amplitude), 0.0), 1.0),
        confidence=min(max(confidence, 0.0), 1.0),
        bend=tuple(step / BEND_STEPS_PER_SEMITONE for step in (bends or [])),
    )
