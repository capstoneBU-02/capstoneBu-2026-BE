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
REFERENCE_LOW_CONFIDENCE = "REFERENCE_LOW_CONFIDENCE"
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
    if reference_unreliable(ref_notes, settings):
        return JudgeResult(not_comparable_reason=REFERENCE_LOW_CONFIDENCE)
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
    merged = _merge(issues, settings.ai_compare_region_merge_ms)
    return JudgeResult(regions=resolve_overlaps(merged))


def reference_unreliable(ref_notes: Sequence[Note], settings: Settings) -> bool:
    """기준 음 자체를 믿기 어려운지. 타브의 LOW_CONFIDENCE_NOTES와 같은 기준이다."""
    low = sum(n.confidence < settings.ai_low_confidence for n in ref_notes)
    return low / len(ref_notes) >= settings.ai_compare_max_low_confidence_ratio


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
    threshold = settings.ai_compare_low_confidence
    if not candidates:
        if ref.confidence < threshold:
            return issue(RegionType.UNAVAILABLE, LOW_CONFIDENCE), False
        return issue(RegionType.MISSING_NOTE), False

    best = min(candidates, key=lambda p: abs(p.start_ms - mapped))
    # 한쪽만 조금 낮아도 보류가 쏟아지지 않도록 두 음의 평균으로 본다
    if (ref.confidence + best.confidence) / 2 < threshold:
        return issue(RegionType.UNAVAILABLE, LOW_CONFIDENCE), True
    if not same:
        return issue(RegionType.PITCH_DIFFERENCE), True
    expected = line[0] * ref.start_ms + line[1]  # 전체 빠르기만 반영한 예상 위치
    if abs(best.start_ms - expected) > settings.ai_compare_timing_ms:
        return issue(RegionType.TIMING_DIFFERENCE), True
    return None, True


def _merge(issues: Sequence[_Issue], gap_ms: int) -> list[Region]:
    """같은 종류·같은 이유의 구간이 gap_ms 이내로 이어지면 하나로 합친다.

    사이에 다른 종류 구간이 끼어 있어도 합친다. 겹치는 부분은 resolve_overlaps가 정리한다.
    """
    regions: list[Region] = []
    latest: dict[tuple[str, str | None], int] = {}  # (종류, 이유) → regions에서 마지막 위치
    for item in sorted(issues, key=lambda i: (i.ref.start_ms, i.type)):
        key = (str(item.type), item.reason)
        index = latest.get(key)
        if index is not None and item.ref.start_ms - regions[index].end_ms <= gap_ms:
            last = regions[index]
            regions[index] = Region(
                type=last.type,
                start_ms=last.start_ms,
                end_ms=max(last.end_ms, item.ref.end_ms),
                performance_start_ms=last.performance_start_ms,
                performance_end_ms=max(last.performance_end_ms, item.perf_end_ms),
                reason=last.reason,
            )
            continue
        latest[key] = len(regions)
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


# 겹치면 앞쪽 종류를 남긴다. 확실한 차이를 판단 보류보다 먼저 보여 준다.
PRIORITY = (
    RegionType.PITCH_DIFFERENCE,
    RegionType.MISSING_NOTE,
    RegionType.TIMING_DIFFERENCE,
    RegionType.UNAVAILABLE,
)


def resolve_overlaps(regions: Sequence[Region]) -> list[Region]:
    """기준 시간축에서 구간이 겹치지 않게 한다.

    우선순위가 높은 구간을 먼저 두고, 낮은 구간은 겹친 부분을 잘라 낸다. 다 덮이면 없앤다.
    """
    order = {str(kind): rank for rank, kind in enumerate(PRIORITY)}
    kept: list[Region] = []
    for region in sorted(regions, key=lambda r: (order.get(r.type, len(order)), r.start_ms)):
        pieces = [region]
        for higher in kept:
            pieces = [part for piece in pieces for part in _subtract(piece, higher)]
        kept.extend(pieces)
    return sorted(kept, key=lambda r: (r.start_ms, r.end_ms))


def _subtract(region: Region, other: Region) -> list[Region]:
    """region에서 other와 겹친 부분을 뺀 나머지 (0~2개)."""
    if other.end_ms <= region.start_ms or other.start_ms >= region.end_ms:
        return [region]
    parts = []
    if region.start_ms < other.start_ms:
        parts.append(_slice(region, region.start_ms, other.start_ms))
    if other.end_ms < region.end_ms:
        parts.append(_slice(region, other.end_ms, region.end_ms))
    return parts


def _slice(region: Region, start_ms: int, end_ms: int) -> Region:
    """기준 시간 일부만 남기고, 연주 시간은 같은 비율로 자른다."""
    span = region.end_ms - region.start_ms
    perf_span = region.performance_end_ms - region.performance_start_ms

    def perf(ms: int) -> int:
        if span <= 0:
            return region.performance_start_ms
        return round(region.performance_start_ms + perf_span * (ms - region.start_ms) / span)

    return Region(
        type=region.type,
        start_ms=start_ms,
        end_ms=end_ms,
        performance_start_ms=perf(start_ms),
        performance_end_ms=perf(end_ms),
        reason=region.reason,
    )
