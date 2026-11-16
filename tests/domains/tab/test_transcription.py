from pathlib import Path

import pytest

from app.core.config import Settings
from app.domains.tab.pipeline import transcription
from app.domains.tab.pipeline.transcription import RawNote, model_path, postprocess
from app.domains.tab.schemas import PlayingCondition

SETTINGS = Settings(_env_file=None)
E_STANDARD = PlayingCondition()


def raw(
    pitch: int, start: float, end: float, confidence: float = 0.9, bend: tuple[float, ...] = ()
) -> RawNote:
    return RawNote(
        start=start, end=end, pitch=pitch, velocity=0.5, confidence=confidence, bend=bend
    )


def test_empty_input() -> None:
    assert postprocess([], E_STANDARD, SETTINGS) == []


def test_notes_below_lowest_open_string_are_removed() -> None:
    notes = postprocess([raw(39, 0.0, 0.5), raw(40, 0.0, 0.5)], E_STANDARD, SETTINGS)

    assert [note.pitch for note in notes] == [40]


def test_capo_raises_lowest_pitch() -> None:
    notes = postprocess([raw(41, 0.0, 0.5)], PlayingCondition(capo=2), SETTINGS)

    assert notes == []


def test_short_gap_of_same_pitch_is_merged() -> None:
    notes = postprocess(
        [raw(64, 0.0, 0.5, 0.8, (0.0,)), raw(64, 0.52, 1.0, 0.4, (1.0,))], E_STANDARD, SETTINGS
    )

    assert len(notes) == 1
    assert (notes[0].start, notes[0].end) == (0.0, 1.0)
    assert notes[0].bend == (0.0, 1.0)
    assert notes[0].confidence == pytest.approx((0.8 * 0.5 + 0.4 * 0.48) / 0.98)


def test_longer_gap_keeps_notes_apart() -> None:
    notes = postprocess([raw(64, 0.0, 0.5), raw(64, 0.6, 1.0)], E_STANDARD, SETTINGS)

    assert len(notes) == 2


def test_different_pitches_are_not_merged() -> None:
    notes = postprocess([raw(64, 0.0, 0.5), raw(65, 0.51, 1.0)], E_STANDARD, SETTINGS)

    assert [note.pitch for note in notes] == [64, 65]


def test_more_than_six_sounding_notes_drops_lowest_confidence() -> None:
    confidences = [0.9, 0.8, 0.7, 0.6, 0.5, 0.95, 0.3]
    chord = [raw(50 + i, 0.0, 1.0, c) for i, c in enumerate(confidences)]

    notes = postprocess(chord, E_STANDARD, SETTINGS)

    assert len(notes) == 6
    assert 56 not in [note.pitch for note in notes]


def test_polyphony_limit_counts_notes_still_ringing() -> None:
    held = [raw(50 + i, 0.0, 2.0, 0.9) for i in range(6)]
    late = raw(60, 1.0, 1.5, 0.2)

    notes = postprocess([*held, late], E_STANDARD, SETTINGS)

    assert 60 not in [note.pitch for note in notes]


def test_notes_after_others_end_are_kept() -> None:
    held = [raw(50 + i, 0.0, 1.0, 0.9) for i in range(6)]

    notes = postprocess([*held, raw(60, 1.0, 1.5, 0.2)], E_STANDARD, SETTINGS)

    assert len(notes) == 7


def test_low_confidence_is_marked_not_removed() -> None:
    notes = postprocess([raw(64, 0.0, 0.5, 0.39), raw(65, 0.6, 1.0, 0.4)], E_STANDARD, SETTINGS)

    assert [note.low_confidence for note in notes] == [True, False]


def test_output_is_sorted_by_start() -> None:
    notes = postprocess([raw(70, 1.0, 1.5), raw(60, 0.0, 0.5)], E_STANDARD, SETTINGS)

    assert [note.start for note in notes] == [0.0, 1.0]


def test_fine_tuned_weights_are_read_from_model_cache() -> None:
    settings = Settings(
        _env_file=None, ai_transcription_weights="ft-20261201", ai_model_cache_dir="/models"
    )

    assert model_path(settings) == Path("/models/basic_pitch/ft-20261201.onnx")


def test_transcribe_finds_sine_tones(tmp_path: Path) -> None:
    """실제 모델로 사인파 음을 찾는다. AI 패키지가 없는 CI에서는 건너뛴다."""
    pytest.importorskip("basic_pitch")
    pytest.importorskip("onnxruntime")
    np = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")

    sample_rate = 22050
    pitches = [52, 57, 64]
    audio = np.zeros(int(sample_rate * 3.0), dtype=np.float32)
    for i, pitch in enumerate(pitches):
        t = np.arange(int(sample_rate * 0.8)) / sample_rate
        frequency = 440.0 * 2 ** ((pitch - 69) / 12)
        first = int(i * sample_rate)
        audio[first : first + len(t)] = 0.5 * np.sin(2 * np.pi * frequency * t) * np.exp(-2 * t)
    path = tmp_path / "tones.wav"
    soundfile.write(path, audio, sample_rate)

    notes = transcription.transcribe(str(path), E_STANDARD, SETTINGS)

    found = {note.pitch for note in notes}
    assert set(pitches) <= found
    assert all(0.0 <= note.confidence <= 1.0 for note in notes)
