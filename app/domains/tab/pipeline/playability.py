"""연주 가능성 검사. 운지 최적화 결과를 손 모양 규칙으로 다시 확인한다."""

from collections import Counter, defaultdict
from collections.abc import Sequence

from app.core.config import get_settings
from app.domains.tab.schemas import QuantizedNote, TabNote

SIXTEENTH_NOTE_BEATS = 0.25
BEAT_TOLERANCE = 1e-6  # 부동소수점 오차로 같은 박이 갈라지지 않게 한다


def check_playable(notes: Sequence[TabNote]) -> list[TabNote]:
    """연주할 수 없는 노트를 playable=False로 표시한 새 목록을 돌려준다.

    입력 순서를 유지하고, 이미 playable=False인 노트는 그대로 둔다.
    """
    settings = get_settings()
    unplayable: set[int] = set()
    previous_beat: float | None = None
    previous_center: float | None = None

    for beat, group in group_by_beat(notes):
        unplayable |= _same_string_notes(notes, group)
        pressed = [i for i in group if notes[i].fret > 0]
        if not pressed:
            continue

        frets = [notes[i].fret for i in pressed]
        center = sum(frets) / len(frets)
        too_wide = max(frets) - min(frets) > settings.ai_playable_max_span
        too_far = (
            previous_beat is not None
            and previous_center is not None
            and beat - previous_beat <= SIXTEENTH_NOTE_BEATS + BEAT_TOLERANCE
            and abs(center - previous_center) >= settings.ai_playable_max_move
        )
        if too_wide or too_far:
            unplayable.update(pressed)
        previous_beat, previous_center = beat, center

    return [
        note.model_copy(update={"playable": False}) if i in unplayable else note
        for i, note in enumerate(notes)
    ]


def group_by_beat(notes: Sequence[QuantizedNote]) -> list[tuple[float, list[int]]]:
    """같은 박에 시작하는 노트의 인덱스를 박 순서대로 묶는다."""
    groups: defaultdict[int, list[int]] = defaultdict(list)
    for i, note in enumerate(notes):
        groups[round(note.beat / BEAT_TOLERANCE)].append(i)
    return [(key * BEAT_TOLERANCE, groups[key]) for key in sorted(groups)]


def _same_string_notes(notes: Sequence[TabNote], group: list[int]) -> set[int]:
    """한 줄에 두 음 이상 배정된 노트의 인덱스."""
    counts = Counter(notes[i].string for i in group)
    return {i for i in group if counts[notes[i].string] > 1}
