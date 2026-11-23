"""연주 비교 판정 (AI 설계서 11장). 정렬·음 인식·판정을 작업(tasks.py)이 단계별로 부른다.

- align: 길이·소리 크기 검사 후 크로마·onset 특징을 DTW로 맞춘다
- transcribe_both: 타브와 같은 음 인식(Basic Pitch)으로 양쪽 음을 찾는다
- judge: 정렬한 위치에서 음·타이밍 차이를 찾고, 확신이 없으면 판단 보류로 둔다

판정은 다시 들어 볼 구간을 알려 줄 뿐 정답을 정하지 않는다. 기준값은 get_settings()의 AI 설정이다.
AI 라이브러리는 api·CI에 없으므로 함수 안에서 import한다.
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.domains.compare.models import RegionType
from app.domains.compare.pipeline.contract import (
    AlignmentResult,
    JudgeResult,
    Note,
    NotesResult,
    Region,
)
from app.domains.tab.schemas import Note as TabNote

SAMPLE_RATE = 22050
HOP = 512  # 약 23ms
LOCAL_SLOPE_WINDOW_MS = 500
UNCLEAR_SLOPE = (0.5, 2.0)  # 이 범위 밖으로 국소 기울기가 꺾이면 정렬이 불명확하다
FEATURE_FLOOR = 1e-6

RERECORD_TOO_SHORT = "RERECORD_TOO_SHORT"
RERECORD_TOO_LONG = "RERECORD_TOO_LONG"
RERECORD_TOO_QUIET = "RERECORD_TOO_QUIET"
RERECORD_FEW_NOTES = "RERECORD_FEW_NOTES"
LOW_MATCH_RATE = "LOW_MATCH_RATE"
TEMPO_OUT_OF_RANGE = "TEMPO_OUT_OF_RANGE"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
UNCLEAR_ALIGNMENT = "UNCLEAR_ALIGNMENT"


# ---- align ----


def align(ref_path: str, perf_path: str, settings: Settings) -> AlignmentResult:
    """크로마·onset + DTW로 두 연주의 시간을 맞추고 전체 빠르기를 판정한다.

    처리 전 입력 검사(길이가 구간의 절반 미만·2배 초과, 소리 크기)에 걸리면
    rerecord_reason을 채운다.
    """
    import librosa

    ref, _ = librosa.load(ref_path, sr=SAMPLE_RATE, mono=True)
    perf, _ = librosa.load(perf_path, sr=SAMPLE_RATE, mono=True)
    reason = check_input(
        len(ref) / SAMPLE_RATE, len(perf) / SAMPLE_RATE, _active_ratio(perf, settings), settings
    )
    if reason is not None:
        return AlignmentResult(path=[], slope=1.0, is_slow=False, rerecord_reason=reason)

    _, steps = librosa.sequence.dtw(X=_features(ref), Y=_features(perf), metric="cosine")
    frame_ms = HOP / SAMPLE_RATE * 1000
    path = sorted((round(i * frame_ms), round(j * frame_ms)) for i, j in steps)
    slope = fit_line(path)[0]
    return AlignmentResult(path=path, slope=slope, is_slow=slope >= settings.ai_compare_slow_slope)


def check_input(
    ref_sec: float, perf_sec: float, active_ratio: float, settings: Settings
) -> str | None:
    """다시 녹음이 필요한지. 길이·소리 크기만 본다."""
    if perf_sec < ref_sec * settings.ai_compare_min_length_ratio:
        return RERECORD_TOO_SHORT
    if perf_sec > ref_sec * settings.ai_compare_max_length_ratio:
        return RERECORD_TOO_LONG
    if active_ratio < settings.ai_compare_min_active_ratio:
        return RERECORD_TOO_QUIET
    return None


def _active_ratio(signal: Any, settings: Settings) -> float:
    import librosa
    import numpy as np

    rms = librosa.feature.rms(y=signal, hop_length=HOP)[0]
    threshold = 10 ** (settings.ai_silence_dbfs / 20)
    return float(np.mean(rms > threshold)) if rms.size else 0.0


def _features(signal: Any) -> Any:
    """크로마(음높이)와 onset 세기(타이밍)를 프레임별로 이어 붙인다."""
    import librosa
    import numpy as np

    chroma = librosa.feature.chroma_cqt(y=signal, sr=SAMPLE_RATE, hop_length=HOP)
    onset = librosa.onset.onset_strength(y=signal, sr=SAMPLE_RATE, hop_length=HOP)
    onset = onset / (onset.max() or 1.0)
    frames = min(chroma.shape[1], onset.shape[0])
    features = np.vstack([chroma[:, :frames], onset[np.newaxis, :frames]])
    # 완전한 무음 프레임은 값이 모두 0이라 코사인 거리가 NaN이 되므로 아주 작은 값을 더한다
    return np.nan_to_num(features) + FEATURE_FLOOR


def fit_line(path: Sequence[tuple[int, int]]) -> tuple[float, float]:
    """연주 시각 = 기울기 × 기준 시각 + 절편. 기울기가 1보다 크면 연주가 느리다."""
    if len(path) < 2:
        return 1.0, 0.0
    xs = [p[0] for p in path]
    ys = [p[1] for p in path]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mean_x) ** 2 for x in xs)
    if var == 0:
        return 1.0, mean_y - mean_x
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / var
    return slope, mean_y - slope * mean_x


# ---- transcribe_both ----


def transcribe_both(ref_path: str, perf_path: str, settings: Settings) -> NotesResult:
    """양쪽 연주의 음을 Basic Pitch로 인식한다. 인식 음 수가 기준의 절반 미만이면 다시 녹음."""
    from app.domains.tab.pipeline.transcription import transcribe
    from app.domains.tab.schemas import PlayingCondition

    condition = PlayingCondition()
    reference = [_to_ms(n) for n in transcribe(ref_path, condition, settings)]
    performance = [_to_ms(n) for n in transcribe(perf_path, condition, settings)]
    reason = None
    if reference and len(performance) < len(reference) * settings.ai_compare_min_note_ratio:
        reason = RERECORD_FEW_NOTES
    return NotesResult(reference=reference, performance=performance, rerecord_reason=reason)


def _to_ms(note: TabNote) -> Note:
    """타브 음 인식 결과(초)를 비교 형식(ms 정수)으로 바꾼다."""
    return Note(
        start_ms=round(note.start * 1000),
        end_ms=round(note.end * 1000),
        pitch=note.pitch,
        confidence=note.confidence,
    )


# ---- judge ----


@dataclass(frozen=True)
class _Issue:
    type: str
    ref: Note
    perf_start_ms: int
    perf_end_ms: int
    reason: str | None = None


class _PathMap:
    """DTW 경로로 기준 시각에 대응하는 연주 시각을 찾는다."""

    def __init__(self, path: Sequence[tuple[int, int]]) -> None:
        by_ref: dict[int, list[int]] = {}
        for ref_ms, perf_ms in path:
            by_ref.setdefault(ref_ms, []).append(perf_ms)
        self.refs = sorted(by_ref)
        self.perfs = [sum(by_ref[r]) / len(by_ref[r]) for r in self.refs]

    def __call__(self, ref_ms: float) -> float:
        i = bisect_left(self.refs, ref_ms)
        if i <= 0:
            return self.perfs[0] + (ref_ms - self.refs[0])
        if i >= len(self.refs):
            return self.perfs[-1] + (ref_ms - self.refs[-1])
        x0, x1 = self.refs[i - 1], self.refs[i]
        y0, y1 = self.perfs[i - 1], self.perfs[i]
        return y0 + (y1 - y0) * (ref_ms - x0) / (x1 - x0)

    def local_slope(self, ref_ms: float) -> float:
        before, after = ref_ms - LOCAL_SLOPE_WINDOW_MS, ref_ms + LOCAL_SLOPE_WINDOW_MS
        return (self(after) - self(before)) / (after - before)


def judge(alignment: AlignmentResult, notes: NotesResult, settings: Settings) -> JudgeResult:
    """음·타이밍 차이를 판정하고 판단 보류 구간을 나눈다. 비교할 수 없으면 not_comparable_reason."""
    ref_notes = notes.reference
    if not alignment.path or not ref_notes:
        return JudgeResult(not_comparable_reason=LOW_MATCH_RATE)
    low, high = 1 / settings.ai_compare_max_tempo_ratio, settings.ai_compare_max_tempo_ratio
    if not low <= alignment.slope <= high:
        return JudgeResult(not_comparable_reason=TEMPO_OUT_OF_RANGE)

    mapping = _PathMap(alignment.path)
    slope, intercept = fit_line(alignment.path)
    issues: list[_Issue] = []
    matched = 0
    for ref in ref_notes:
        issue, found = _judge_note(ref, notes.performance, mapping, (slope, intercept), settings)
        matched += found
        if issue is not None:
            issues.append(issue)

    if matched / len(ref_notes) < settings.ai_compare_min_match_rate:
        return JudgeResult(not_comparable_reason=LOW_MATCH_RATE)
    return JudgeResult(regions=_merge(issues, settings.ai_compare_region_merge_ms))


def _judge_note(
    ref: Note,
    performance: Sequence[Note],
    mapping: _PathMap,
    line: tuple[float, float],
    settings: Settings,
) -> tuple[_Issue | None, bool]:
    """기준 음 하나를 판정한다. (구간, 연주에서 짝을 찾았는지)"""
    mapped = mapping(ref.start_ms)
    perf_end = round(mapping(ref.end_ms))
    window = settings.ai_compare_match_window_ms
    nearby = [p for p in performance if abs(p.start_ms - mapped) <= window]
    same = [p for p in nearby if p.pitch == ref.pitch]
    candidates = same or nearby

    def issue(kind: RegionType, reason: str | None = None) -> _Issue:
        return _Issue(kind, ref, round(mapped), max(perf_end, round(mapped)), reason)

    slope_here = mapping.local_slope(ref.start_ms)
    if not UNCLEAR_SLOPE[0] <= slope_here <= UNCLEAR_SLOPE[1]:
        return issue(RegionType.UNAVAILABLE, UNCLEAR_ALIGNMENT), bool(candidates)
    if not candidates:
        if ref.confidence < settings.ai_low_confidence:
            return issue(RegionType.UNAVAILABLE, LOW_CONFIDENCE), False
        return issue(RegionType.MISSING_NOTE), False

    best = min(candidates, key=lambda p: abs(p.start_ms - mapped))
    if min(ref.confidence, best.confidence) < settings.ai_low_confidence:
        return issue(RegionType.UNAVAILABLE, LOW_CONFIDENCE), True
    if not same:
        return issue(RegionType.PITCH_DIFFERENCE), True
    expected = line[0] * ref.start_ms + line[1]  # 전체 빠르기만 반영한 예상 위치
    if abs(best.start_ms - expected) > settings.ai_compare_timing_ms:
        return issue(RegionType.TIMING_DIFFERENCE), True
    return None, True


def _merge(issues: Sequence[_Issue], gap_ms: int) -> list[Region]:
    """같은 종류·같은 이유의 구간이 gap_ms 이내로 이어지면 하나로 합친다."""
    regions: list[Region] = []
    for item in sorted(issues, key=lambda i: (i.ref.start_ms, i.type)):
        last = regions[-1] if regions else None
        if (
            last is not None
            and last.type == item.type
            and last.reason == item.reason
            and item.ref.start_ms - last.end_ms <= gap_ms
        ):
            regions[-1] = Region(
                type=last.type,
                start_ms=last.start_ms,
                end_ms=max(last.end_ms, item.ref.end_ms),
                performance_start_ms=last.performance_start_ms,
                performance_end_ms=max(last.performance_end_ms, item.perf_end_ms),
                reason=last.reason,
            )
            continue
        regions.append(
            Region(
                type=str(item.type),
                start_ms=item.ref.start_ms,
                end_ms=item.ref.end_ms,
                performance_start_ms=item.perf_start_ms,
                performance_end_ms=item.perf_end_ms,
                reason=item.reason,
            )
        )
    return regions
