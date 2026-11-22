from pathlib import Path

import pytest

from app.core.config import Settings
from app.domains.compare.pipeline import judge
from app.domains.compare.pipeline.contract import AlignmentResult, Note, NotesResult

SETTINGS = Settings(_env_file=None)


def note(start: int, pitch: int = 64, length: int = 200, confidence: float = 0.9) -> Note:
    return Note(start_ms=start, end_ms=start + length, pitch=pitch, confidence=confidence)


def straight(slope: float = 1.0, end_ms: int = 10_000) -> list[tuple[int, int]]:
    """기준과 연주가 일정한 빠르기로 대응하는 경로."""
    return [(t, round(t * slope)) for t in range(0, end_ms + 1, 23)]


def run(ref: list[Note], perf: list[Note], slope: float = 1.0) -> judge.JudgeResult:
    alignment = AlignmentResult(path=straight(slope), slope=slope, is_slow=slope >= 1.1)
    return judge.judge(alignment, NotesResult(reference=ref, performance=perf), SETTINGS)


def kinds(result: judge.JudgeResult) -> list[tuple[str, int, str | None]]:
    return [(r.type, r.start_ms, r.reason) for r in result.regions]


REF = [note(1000 + i * 500, 60 + i) for i in range(8)]


# ---- 입력 검사 ----


@pytest.mark.parametrize(
    ("perf_sec", "active", "reason"),
    [
        (4.9, 1.0, "RERECORD_TOO_SHORT"),
        (20.1, 1.0, "RERECORD_TOO_LONG"),
        (10.0, 0.1, "RERECORD_TOO_QUIET"),
        (10.0, 1.0, None),
        (5.0, 0.2, None),
    ],
)
def test_check_input(perf_sec: float, active: float, reason: str | None) -> None:
    assert judge.check_input(10.0, perf_sec, active, SETTINGS) == reason


def test_fit_line_finds_slope_and_offset() -> None:
    slope, intercept = judge.fit_line([(0, 200), (1000, 1400), (2000, 2600)])

    assert slope == pytest.approx(1.2)
    assert intercept == pytest.approx(200)


# ---- 판정 ----


def test_same_performance_has_no_regions() -> None:
    assert run(REF, list(REF)).regions == []


def test_slower_performance_is_not_timing_difference() -> None:
    perf = [note(round(n.start_ms * 1.2), n.pitch) for n in REF]

    assert run(REF, perf, slope=1.2).regions == []


def test_wrong_pitch_is_pitch_difference() -> None:
    perf = list(REF)
    perf[3] = note(REF[3].start_ms, REF[3].pitch + 2)

    assert kinds(run(REF, perf)) == [("pitch_difference", REF[3].start_ms, None)]


def test_missing_note() -> None:
    perf = [n for i, n in enumerate(REF) if i != 5]

    assert kinds(run(REF, perf)) == [("missing_note", REF[5].start_ms, None)]


def test_late_note_is_timing_difference() -> None:
    perf = list(REF)
    perf[2] = note(REF[2].start_ms + 130, REF[2].pitch)

    assert kinds(run(REF, perf)) == [("timing_difference", REF[2].start_ms, None)]


def test_small_timing_error_is_allowed() -> None:
    perf = list(REF)
    perf[2] = note(REF[2].start_ms + 60, REF[2].pitch)

    assert run(REF, perf).regions == []


def test_low_confidence_is_unavailable_not_difference() -> None:
    perf = list(REF)
    perf[4] = note(REF[4].start_ms, REF[4].pitch + 1, confidence=0.2)

    assert kinds(run(REF, perf)) == [("unavailable", REF[4].start_ms, "LOW_CONFIDENCE")]


def test_missing_low_confidence_reference_is_unavailable() -> None:
    ref = list(REF)
    ref[4] = note(REF[4].start_ms, REF[4].pitch, confidence=0.2)
    perf = [n for i, n in enumerate(REF) if i != 4]

    assert kinds(run(ref, perf)) == [("unavailable", REF[4].start_ms, "LOW_CONFIDENCE")]


def test_unclear_alignment_is_unavailable() -> None:
    path = (
        [(t, t) for t in range(0, 2600, 23)]
        + [(2600, 2600)]
        + [(t, 6000 + t) for t in range(2623, 10000, 23)]
    )
    alignment = AlignmentResult(path=path, slope=1.0, is_slow=False)

    result = judge.judge(alignment, NotesResult(reference=REF, performance=list(REF)), SETTINGS)

    assert any(r.reason == "UNCLEAR_ALIGNMENT" for r in result.regions)


def test_neighboring_same_regions_are_merged() -> None:
    perf = [n for i, n in enumerate(REF) if i not in (3, 4)]

    result = run(REF, perf)

    assert kinds(result) == [("missing_note", REF[3].start_ms, None)]
    assert result.regions[0].end_ms == REF[4].end_ms


def test_regions_have_performance_times() -> None:
    perf = [note(round(n.start_ms * 1.2), n.pitch) for i, n in enumerate(REF) if i != 6]

    region = run(REF, perf, slope=1.2).regions[0]

    assert region.performance_start_ms == round(REF[6].start_ms * 1.2)


def test_low_match_rate_is_not_comparable() -> None:
    result = run(REF, [note(9000, 40)])

    assert (result.not_comparable_reason, result.regions) == ("LOW_MATCH_RATE", [])


def test_tempo_out_of_range_is_not_comparable() -> None:
    assert run(REF, list(REF), slope=1.6).not_comparable_reason == "TEMPO_OUT_OF_RANGE"


def test_empty_reference_or_path_is_not_comparable() -> None:
    empty = AlignmentResult(path=[], slope=1.0, is_slow=False)

    assert (
        judge.judge(empty, NotesResult(REF, REF), SETTINGS).not_comparable_reason
        == "LOW_MATCH_RATE"
    )
    assert run([], REF).not_comparable_reason == "LOW_MATCH_RATE"


# ---- 실제 라이브러리 (AI 패키지가 없는 CI에서는 건너뜀) ----


def _write(tmp: Path, name: str, pitches: list[int], gap: float, stretch: float = 1.0) -> str:
    np = pytest.importorskip("numpy")
    soundfile = pytest.importorskip("soundfile")
    sr = judge.SAMPLE_RATE
    length = gap * stretch
    audio = np.zeros(int(sr * (len(pitches) * length + 1.0)), dtype=np.float32)
    for i, pitch in enumerate(pitches):
        t = np.arange(int(sr * length * 0.9)) / sr
        tone = np.sin(2 * np.pi * 440 * 2 ** ((pitch - 69) / 12) * t) * np.exp(-3 * t)
        first = int((0.5 + i * length) * sr)
        audio[first : first + len(tone)] += 0.5 * tone
    path = tmp / name
    soundfile.write(path, audio, sr)
    return str(path)


def test_align_measures_overall_tempo(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    pitches = [60, 64, 67, 72, 67, 64, 60, 62, 65, 69, 65, 62]
    ref = _write(tmp_path, "ref.wav", pitches, 0.4)
    slow = _write(tmp_path, "slow.wav", pitches, 0.4, stretch=1.25)

    same = judge.align(ref, ref, SETTINGS)
    result = judge.align(ref, slow, SETTINGS)

    assert same.slope == pytest.approx(1.0, abs=0.05)
    assert not same.is_slow
    assert result.slope == pytest.approx(1.25, abs=0.1)
    assert result.is_slow
    assert result.rerecord_reason is None


def test_align_handles_exact_silence(tmp_path: Path) -> None:
    """앞뒤가 완전한 무음(0)이어도 거리 계산이 NaN으로 실패하지 않는다."""
    pytest.importorskip("librosa")
    import numpy as np
    import soundfile

    pitches = [60, 64, 67, 72, 67, 64, 60, 62]
    ref = _write(tmp_path, "ref.wav", pitches, 0.4)
    signal, sr = soundfile.read(ref)
    silence = np.zeros(sr)
    padded = tmp_path / "padded.wav"
    soundfile.write(padded, np.concatenate([silence, signal, silence]), sr)

    result = judge.align(str(padded), ref, SETTINGS)

    assert result.rerecord_reason is None
    assert result.path


def test_align_asks_rerecord_for_short_performance(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    ref = _write(tmp_path, "ref.wav", [60, 64, 67, 72] * 3, 0.4)
    short = _write(tmp_path, "short.wav", [60, 64], 0.4)

    result = judge.align(ref, short, SETTINGS)

    assert (result.rerecord_reason, result.path) == ("RERECORD_TOO_SHORT", [])
