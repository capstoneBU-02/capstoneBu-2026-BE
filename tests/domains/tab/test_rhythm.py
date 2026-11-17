from pathlib import Path

import pytest

from app.core.config import Settings
from app.domains.tab.pipeline import rhythm
from app.domains.tab.pipeline.rhythm import BeatGrid, RhythmError, quantize
from app.domains.tab.schemas import Note

# 120 BPM: 0.5초마다 박, 4박마다 마디 첫 박
BEATS = tuple(i * 0.5 for i in range(17))
GRID = BeatGrid(beats=BEATS, downbeats=BEATS[::4])


def note(start: float, end: float, pitch: int = 64) -> Note:
    return Note(start=start, end=end, pitch=pitch, velocity=0.5, confidence=0.9)


def positions(notes: list[Note], grid: BeatGrid = GRID) -> list[tuple[float, float]]:
    return [(n.beat, n.duration) for n in quantize(notes, grid).notes]


def test_notes_on_the_beat() -> None:
    assert positions([note(0.0, 0.5), note(0.5, 1.0)]) == [(0.0, 1.0), (1.0, 1.0)]


def test_slightly_early_or_late_notes_snap_to_sixteenth_grid() -> None:
    assert positions([note(0.13, 0.25), note(0.26, 0.5)]) == [(0.25, 0.25), (0.5, 0.5)]


def test_triplet_is_used_when_closer() -> None:
    result = positions([note(0.0, 0.16), note(0.167, 0.33), note(0.333, 0.5)])

    assert [beat for beat, _ in result] == pytest.approx([0.0, 1 / 3, 2 / 3])
    assert [duration for _, duration in result] == pytest.approx([1 / 3, 1 / 3, 1 / 3])


def test_duration_stops_at_next_onset() -> None:
    assert positions([note(0.0, 2.0), note(0.5, 1.0)]) == [(0.0, 1.0), (1.0, 1.0)]


def test_chord_notes_share_start_and_keep_own_length() -> None:
    result = positions([note(0.0, 1.0, 60), note(0.0, 0.5, 64)])

    assert result == [(0.0, 2.0), (0.0, 1.0)]


def test_very_short_note_gets_one_grid_unit() -> None:
    assert positions([note(1.0, 1.01)]) == [(2.0, 0.25)]


def test_first_downbeat_is_beat_zero() -> None:
    grid = BeatGrid(beats=BEATS, downbeats=BEATS[1::4])

    assert positions([note(0.5, 1.0)], grid) == [(0.0, 1.0)]


def test_pickup_note_moves_origin_back_one_bar() -> None:
    grid = BeatGrid(beats=BEATS, downbeats=BEATS[2::4])

    assert positions([note(0.5, 1.0), note(1.0, 1.5)], grid) == [(3.0, 1.0), (4.0, 1.0)]


def test_notes_outside_beats_are_extrapolated() -> None:
    assert positions([note(8.5, 9.0)]) == [(17.0, 1.0)]


def test_tempo_and_four_four_meter() -> None:
    result = quantize([note(0.0, 0.5)], GRID)

    assert result.tempo_bpm == pytest.approx(120.0)
    assert (result.beats_per_bar, result.non_4_4) == (4, False)


def test_three_four_meter_is_flagged() -> None:
    result = quantize([note(0.0, 0.5)], BeatGrid(beats=BEATS, downbeats=BEATS[::3]))

    assert (result.beats_per_bar, result.non_4_4) == (3, True)


def test_meter_is_unknown_with_one_downbeat() -> None:
    result = quantize([note(0.0, 0.5)], BeatGrid(beats=BEATS, downbeats=BEATS[:1]))

    assert (result.beats_per_bar, result.non_4_4) == (None, False)


def test_fewer_than_two_beats_fails() -> None:
    with pytest.raises(RhythmError):
        quantize([note(0.0, 0.5)], BeatGrid(beats=(0.0,)))


def test_input_order_and_fields_are_kept() -> None:
    notes = [note(1.0, 1.5, 67), note(0.0, 0.5, 60)]

    result = quantize(notes, GRID).notes

    assert [n.pitch for n in result] == [67, 60]
    assert result[0].confidence == 0.9


def test_estimate_beats_falls_back_to_librosa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beat This!를 못 쓰면 librosa로 박을 찾는다. AI 패키지가 없는 CI에서는 건너뛴다."""
    np = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")

    sample_rate = 22050
    pad_ms = 500
    audio = np.zeros(int(sample_rate * 10.5), dtype=np.float32)
    click = np.sin(2 * np.pi * 1000 * np.arange(int(0.03 * sample_rate)) / sample_rate)
    for i in range(20):  # 구간 시작(0.5초)부터 0.5초마다 클릭
        first = int((pad_ms / 1000 + i * 0.5) * sample_rate)
        audio[first : first + len(click)] = click
    path = tmp_path / "input.wav"
    soundfile.write(path, audio, sample_rate)

    def broken(_: str) -> object:
        raise RuntimeError("no model")

    monkeypatch.setattr(rhythm, "_load_beat_model", broken)
    grid = rhythm.estimate_beats(str(path), pad_ms, 10_000, Settings(_env_file=None))

    intervals = [b - a for a, b in zip(grid.beats, grid.beats[1:], strict=False)]
    assert len(grid.beats) >= 10
    assert sorted(intervals)[len(intervals) // 2] == pytest.approx(0.5, abs=0.03)
    # 앞쪽 여유를 빼서 클릭이 구간 기준 0.5초 격자에 놓인다 (librosa는 첫 박을 건너뛰기도 한다)
    assert all(abs(t / 0.5 - round(t / 0.5)) < 0.1 for t in grid.beats)
    assert grid.downbeats == grid.beats[::4]
