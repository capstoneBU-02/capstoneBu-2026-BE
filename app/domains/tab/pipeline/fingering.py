"""타브 운지 변환. 음마다 줄·프렛을 고르는 문제를 시간 순서의 최단 경로로 보고 Viterbi로 푼다.

시점은 같은 박에 시작하는 노트 묶음이고, 상태는 묶음의 각 음에 줄·프렛을 배정한 손 모양이다.
상태 비용은 프렛 범위와 손 위치, 전이 비용은 손 이동과 건너뛴 줄 수로 계산한다.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from app.core.config import Settings, get_settings
from app.domains.tab.pipeline.playability import group_by_beat
from app.domains.tab.schemas import PlayingCondition, QuantizedNote, TabNote

NOTE_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACCIDENTALS = {"": 0, "#": 1, "b": -1}
NOTE_NAME = re.compile(r"([A-G])([#b]?)(-?\d+)")


class _Position(NamedTuple):
    string: int
    fret: int


@dataclass(frozen=True, slots=True)
class _State:
    """한 시점의 손 모양. positions는 묶음의 노트 순서와 같다."""

    positions: tuple[_Position, ...]
    cost: float
    center: float | None  # 누르는 프렛 평균. 개방현만 있으면 None


@dataclass(frozen=True, slots=True)
class _PathEntry:
    state: _State
    cost: float
    hand: float | None  # 개방현만 치는 시점을 지나도 유지되는 마지막 손 위치
    previous: "_PathEntry | None"


def assign_fingering(notes: Sequence[QuantizedNote], condition: PlayingCondition) -> list[TabNote]:
    """노트마다 줄·프렛을 배정한다. 입력 순서를 유지한다.

    연주 범위를 벗어난 음은 지우지 않고 가장 가까운 위치에 두어 playable=False로 표시한다.
    """
    groups = group_by_beat(notes)
    if not groups:
        return []

    settings = get_settings()
    open_pitches = _open_pitches(condition)
    state_lists = [
        _build_states([notes[i].pitch for i in indices], open_pitches, settings)
        for _, indices in groups
    ]

    positions: dict[int, _Position] = {}
    path = _best_path(state_lists, settings.ai_tab_weights)
    for (_, indices), state in zip(groups, path, strict=True):
        positions.update(zip(indices, state.positions, strict=True))

    return [_to_tab_note(note, positions[i], open_pitches) for i, note in enumerate(notes)]


def pitch_from_name(name: str) -> int:
    """음 이름을 MIDI 번호로 바꾼다. 예) E2 → 40, F#3 → 54"""
    match = NOTE_NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"음 이름 형식이 잘못되었습니다: {name}")
    letter, accidental, octave = match.groups()
    return 12 * (int(octave) + 1) + NOTE_SEMITONES[letter] + ACCIDENTALS[accidental]


def _open_pitches(condition: PlayingCondition) -> dict[int, int]:
    """줄 번호별 개방현 음높이. tuning은 저음부터이고 줄 번호는 고음이 1이다."""
    return {
        condition.strings - i: pitch_from_name(name) + condition.capo
        for i, name in enumerate(condition.tuning)
    }


def _build_states(
    pitches: Sequence[int], open_pitches: Mapping[int, int], settings: Settings
) -> list[_State]:
    """한 시점의 손 모양 후보를 비용 낮은 순으로 만든다."""
    candidates = [_candidates(pitch, open_pitches, settings.ai_tab_max_fret) for pitch in pitches]
    assignments: list[tuple[_Position, ...]] = []
    if len(pitches) <= len(open_pitches):
        assignments = list(_distinct_assignments(candidates))
    if not assignments:
        # 줄이 겹칠 수밖에 없으면 낮은 프렛으로 두고 check_playable이 표시하게 한다
        assignments = [tuple(min(options, key=lambda p: p.fret) for options in candidates)]

    states = [_to_state(positions, settings) for positions in assignments]
    states.sort(key=lambda state: (state.cost, state.positions))
    return states[: settings.ai_tab_max_states]


def _candidates(pitch: int, open_pitches: Mapping[int, int], max_fret: int) -> list[_Position]:
    positions = [
        _Position(string, pitch - open_pitch)
        for string, open_pitch in open_pitches.items()
        if 0 <= pitch - open_pitch <= max_fret
    ]
    if positions:
        return positions

    string = min(
        open_pitches,
        key=lambda s: (
            abs(pitch - open_pitches[s] - _clamp_fret(pitch - open_pitches[s], max_fret)),
            s,
        ),
    )
    return [_Position(string, _clamp_fret(pitch - open_pitches[string], max_fret))]


def _clamp_fret(fret: int, max_fret: int) -> int:
    return min(max(fret, 0), max_fret)


def _distinct_assignments(
    candidates: Sequence[Sequence[_Position]], used: frozenset[int] = frozenset()
) -> Iterator[tuple[_Position, ...]]:
    """한 줄에 한 음만 오도록 후보를 조합한다."""
    if not candidates:
        yield ()
        return
    for position in candidates[0]:
        if position.string in used:
            continue
        for rest in _distinct_assignments(candidates[1:], used | {position.string}):
            yield (position, *rest)


def _to_state(positions: tuple[_Position, ...], settings: Settings) -> _State:
    frets = [position.fret for position in positions if position.fret > 0]
    if not frets:
        return _State(positions, 0.0, None)

    weights = settings.ai_tab_weights
    span = max(frets) - min(frets)
    center = sum(frets) / len(frets)
    cost = weights["span"] * span + weights["pos"] * center
    cost += settings.ai_tab_span_penalty * max(span - settings.ai_tab_span_limit, 0)
    return _State(positions, cost, center)


def _best_path(
    state_lists: Sequence[Sequence[_State]], weights: Mapping[str, float]
) -> list[_State]:
    """시점별 후보에서 전체 비용이 가장 낮은 경로를 찾는다."""
    entries = [_PathEntry(state, state.cost, state.center, None) for state in state_lists[0]]
    for states in state_lists[1:]:
        entries = [_extend(entries, state, weights) for state in states]

    entry: _PathEntry | None = min(entries, key=lambda e: e.cost)
    path: list[_State] = []
    while entry is not None:
        path.append(entry.state)
        entry = entry.previous
    path.reverse()
    return path


def _extend(
    previous: Sequence[_PathEntry], state: _State, weights: Mapping[str, float]
) -> _PathEntry:
    """이전 시점에서 이 상태로 오는 가장 싼 경로를 고른다."""
    best = min(previous, key=lambda entry: entry.cost + _transition_cost(entry, state, weights))
    cost = best.cost + _transition_cost(best, state, weights) + state.cost
    hand = state.center if state.center is not None else best.hand
    return _PathEntry(state, cost, hand, best)


def _transition_cost(entry: _PathEntry, state: _State, weights: Mapping[str, float]) -> float:
    move = 0.0
    if entry.hand is not None and state.center is not None:
        move = abs(state.center - entry.hand)
    skipped = _skipped_strings(entry.state.positions, state.positions)
    return weights["move"] * move + weights["strskip"] * skipped


def _skipped_strings(previous: Sequence[_Position], current: Sequence[_Position]) -> int:
    """두 시점의 가장 가까운 줄 사이에 건너뛴 줄 수."""
    gap = min(abs(a.string - b.string) for a in previous for b in current)
    return max(gap - 1, 0)


def _to_tab_note(
    note: QuantizedNote, position: _Position, open_pitches: Mapping[int, int]
) -> TabNote:
    return TabNote(
        **note.model_dump(),
        string=position.string,
        fret=position.fret,
        playable=open_pitches[position.string] + position.fret == note.pitch,
    )
