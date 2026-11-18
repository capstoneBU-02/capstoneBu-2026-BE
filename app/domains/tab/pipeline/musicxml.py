"""MusicXML 생성. 줄·프렛이 정해진 노트를 4/4 기타 타브 악보로 만든다 (AI 설계서 9.3).

마디 나누기·붙임줄·셋잇단·쉼표는 music21에 맡긴다. music21은 화음 안 음의 줄·프렛을 내보내지 않아서,
내보낸 XML을 다시 읽어 모든 음에 <technical><string>·<fret>을 직접 넣는다.
AI 라이브러리는 api·CI에 없으므로 함수 안에서 import한다.
"""

import importlib
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from fractions import Fraction
from itertools import groupby
from typing import Any

from app.domains.tab.pipeline.fingering import NOTE_NAME
from app.domains.tab.schemas import PlayingCondition, TabNote

GRID = 12  # 한 박을 12칸으로 나누면 16분음표(3칸)와 셋잇단(4칸)이 모두 정수다
STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ALTER = {"": 0, "#": 1, "b": -1}


def build_musicxml(
    notes: Sequence[TabNote], condition: PlayingCondition, tempo_bpm: float
) -> bytes:
    """MusicXML 문서(UTF-8)를 만든다. 노트는 rhythm 단계에서 박 위치·길이가 정해져 있어야 한다."""
    exported = _export_with_music21(notes, condition, tempo_bpm)
    header, _, body = exported.partition("<score-partwise")
    root = ET.fromstring("<score-partwise" + body)
    _add_staff_details(root, condition)
    _add_string_and_fret(root, notes)
    return (header + ET.tostring(root, encoding="unicode")).encode()


def _fraction(beats: float) -> Fraction:
    return Fraction(round(beats * GRID), GRID)


def _export_with_music21(
    notes: Sequence[TabNote], condition: PlayingCondition, tempo_bpm: float
) -> str:
    # music21은 타입 정보가 일부만 있어 설치된 환경에서 mypy가 막으므로 Any로 다룬다
    m21: Any = importlib.import_module("music21")
    exporter: Any = importlib.import_module("music21.musicxml.m21ToXml").GeneralObjectExporter

    part = m21.stream.Part()
    part.insert(0, m21.clef.TabClef())
    part.insert(0, m21.layout.StaffLayout(staffLines=condition.strings))
    part.insert(0, m21.meter.TimeSignature("4/4"))
    part.insert(0, m21.tempo.MetronomeMark(number=round(tempo_bpm)))

    by_start = groupby(sorted(notes, key=lambda n: n.beat), key=lambda n: _fraction(n.beat))
    groups = [(start, list(group)) for start, group in by_start]
    for i, (start, group) in enumerate(groups):
        length = max(_fraction(n.duration) for n in group)
        if i + 1 < len(groups):
            # 한 성부로 그리므로 다음 음과 겹치지 않게 자른다
            length = min(length, groups[i + 1][0] - start)
        pitches = sorted({n.pitch for n in group})
        element = m21.note.Note(pitches[0]) if len(pitches) == 1 else m21.chord.Chord(pitches)
        element.quarterLength = length
        part.insert(start, element)

    return str(exporter(m21.stream.Score([part])).parse().decode())


def _add_staff_details(root: ET.Element, condition: PlayingCondition) -> None:
    """타브 보표의 줄 수, 줄별 개방현 음, 카포. line 1이 가장 낮은 줄이다."""
    attributes = root.find("part/measure/attributes")
    if attributes is None:
        return
    details = attributes.find("staff-details")
    if details is None:
        details = ET.SubElement(attributes, "staff-details")
    for child in list(details):
        if child.tag in ("staff-tuning", "capo"):
            details.remove(child)

    for line, name in enumerate(condition.tuning, start=1):
        match = NOTE_NAME.fullmatch(name)
        if match is None:
            raise ValueError(f"튜닝 음 이름이 잘못되었습니다: {name}")
        step, accidental, octave = match.groups()
        tuning = ET.SubElement(details, "staff-tuning", line=str(line))
        ET.SubElement(tuning, "tuning-step").text = step
        if accidental:
            ET.SubElement(tuning, "tuning-alter").text = str(ALTER[accidental])
        ET.SubElement(tuning, "tuning-octave").text = octave
    if condition.capo:
        ET.SubElement(details, "capo").text = str(condition.capo)


def _add_string_and_fret(root: ET.Element, notes: Sequence[TabNote]) -> None:
    """모든 음(붙임줄로 나뉜 뒷부분 포함)에 그 시각에 울리는 같은 음높이 노트의 줄·프렛을 넣는다."""
    divisions = 1
    time = 0
    onset = 0
    for measure in root.iter("measure"):
        for element in measure:
            if element.tag == "attributes" and element.findtext("divisions"):
                divisions = int(element.findtext("divisions") or divisions)
            elif element.tag == "backup":
                time -= int(element.findtext("duration") or 0)
            elif element.tag == "forward":
                time += int(element.findtext("duration") or 0)
            elif element.tag == "note":
                if element.find("chord") is None:
                    onset = time
                    time += int(element.findtext("duration") or 0)
                pitch = _midi(element)
                if pitch is not None:
                    _set_technical(element, _find(notes, pitch, Fraction(onset, divisions)))


def _midi(element: ET.Element) -> int | None:
    pitch = element.find("pitch")
    if pitch is None:
        return None
    step = pitch.findtext("step") or "C"
    alter = round(float(pitch.findtext("alter") or 0))
    octave = int(pitch.findtext("octave") or 4)
    return 12 * (octave + 1) + STEP_SEMITONES[step] + alter


def _find(notes: Sequence[TabNote], pitch: int, time: Fraction) -> TabNote | None:
    """time에 울리고 있는 같은 음높이 노트. 붙임줄 뒷부분도 원래 노트로 찾는다."""
    for note in notes:
        start = _fraction(note.beat)
        if note.pitch == pitch and start <= time < start + max(
            _fraction(note.duration), Fraction(1, GRID)
        ):
            return note
    return None


def _set_technical(element: ET.Element, note: TabNote | None) -> None:
    if note is None:
        return
    notations = element.find("notations")
    if notations is None:
        notations = ET.SubElement(element, "notations")
    for old in notations.findall("technical"):
        notations.remove(old)
    technical = ET.Element("technical")
    ET.SubElement(technical, "string").text = str(note.string)
    ET.SubElement(technical, "fret").text = str(note.fret)
    notations.insert(0, technical)
