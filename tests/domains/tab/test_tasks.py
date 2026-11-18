import json
from collections.abc import Callable
from pathlib import Path

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.exceptions import ErrorCode
from app.domains.processing.models import Stem
from app.domains.tab import keys, service, tasks
from app.domains.tab.models import Tab, TabStage, TabStageResult, TabStatus
from app.domains.tab.pipeline import rhythm, transcription
from app.domains.tab.pipeline.rhythm import BeatGrid
from app.domains.tab.schemas import Note, TabCreateRequest
from app.domains.tab.tasks import StageError, StageOutcome, run_stage
from tests.domains.conftest import EnqueueSpy

WS = 10


@pytest.fixture
def tab(db: Session, ready_stem: Callable[..., Stem], enqueue: EnqueueSpy) -> Tab:
    ready_stem()
    return service.request_tab(db, WS, 1, TabCreateRequest(), enqueue=enqueue).tab


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """저장소 대역. download_file은 키가 있으면 빈 파일을 쓴다."""
    files: dict[str, bytes] = {
        "10/100/stems/1/1/guitar.wav": b"",
        "10/100/segments/1/1/input.wav": b"",
    }

    def download(key: str, path: str) -> None:
        if key not in files:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "GetObject")
        Path(path).write_bytes(files[key])

    monkeypatch.setattr(storage, "download_file", download)
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, _: files.__setitem__(key, data))
    monkeypatch.setattr(storage, "read_bytes", lambda key: files[key])
    return files


def _reload(db: Session, tab: Tab) -> Tab:
    db.expire_all()
    t = db.get(Tab, tab.id)
    assert t is not None
    return t


def _results(db: Session, tab_id: int) -> list[tuple[str, str, int, str | None]]:
    rows = db.scalars(
        select(TabStageResult).where(TabStageResult.tab_id == tab_id).order_by(TabStageResult.id)
    ).all()
    return [(r.stage, r.status, r.attempt, r.error_code) for r in rows]


def ok(key: str, reasons: list[str] | None = None) -> Callable[..., StageOutcome]:
    def work(db: Session, tab: Tab, tmp: Path) -> StageOutcome:
        assert tmp.is_dir()
        return StageOutcome(output_key=key, model="m", review_reasons=reasons or [])

    return work


def fail(exc: BaseException) -> Callable[..., StageOutcome]:
    def work(db: Session, tab: Tab, tmp: Path) -> StageOutcome:
        raise exc

    return work


def test_full_pipeline_success(
    db: Session, tab: Tab, session_factory: Callable[[], Session]
) -> None:
    assert run_stage(tab.id, TabStage.TRANSCRIPTION, ok("n"), session_factory)
    assert _reload(db, tab).status == TabStatus.QUEUED
    assert run_stage(tab.id, TabStage.RHYTHM, ok("r"), session_factory)
    assert run_stage(tab.id, TabStage.TAB, ok("t"), session_factory)

    assert _reload(db, tab).status == TabStatus.SUCCESS
    assert [r[:2] for r in _results(db, tab.id)] == [
        ("transcription", "success"),
        ("rhythm", "success"),
        ("tab", "success"),
    ]


def test_review_reasons_make_needs_review_and_keep_earlier_reasons(
    db: Session, tab: Tab, session_factory: Callable[[], Session]
) -> None:
    tab.review_reasons = ["STEM_NEEDS_REVIEW"]
    db.commit()
    run_stage(tab.id, TabStage.TRANSCRIPTION, ok("n"), session_factory)
    run_stage(tab.id, TabStage.RHYTHM, ok("r"), session_factory)
    run_stage(tab.id, TabStage.TAB, ok("t", ["UNPLAYABLE_POSITIONS"]), session_factory)

    t = _reload(db, tab)
    assert t.status == TabStatus.NEEDS_REVIEW
    assert t.review_reasons == ["STEM_NEEDS_REVIEW", "UNPLAYABLE_POSITIONS"]


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (StageError(ErrorCode.TAB_NO_NOTES), "TAB_NO_NOTES"),
        (SoftTimeLimitExceeded(), "AI_WORKER_TIMEOUT"),
        (RuntimeError("boom"), "AI_WORKER_ERROR"),
    ],
)
def test_failure_codes(
    db: Session, tab: Tab, session_factory: Callable[[], Session], exc: BaseException, code: str
) -> None:
    assert not run_stage(tab.id, TabStage.TRANSCRIPTION, fail(exc), session_factory)

    t = _reload(db, tab)
    assert (t.status, t.error_code, t.current_stage) == ("failed", code, "transcription")
    assert _results(db, tab.id) == [("transcription", "failed", 1, code)]


def test_redelivered_finished_stage_is_skipped(
    db: Session, tab: Tab, session_factory: Callable[[], Session]
) -> None:
    run_stage(tab.id, TabStage.TRANSCRIPTION, ok("n"), session_factory)

    assert run_stage(tab.id, TabStage.TRANSCRIPTION, fail(RuntimeError()), session_factory)
    assert len(_results(db, tab.id)) == 1


def test_failed_tab_is_not_run(
    db: Session, tab: Tab, session_factory: Callable[[], Session]
) -> None:
    tab.status = TabStatus.FAILED
    db.commit()

    assert not run_stage(tab.id, TabStage.TRANSCRIPTION, ok("n"), session_factory)
    assert _results(db, tab.id) == []


def _note(start: float, end: float, pitch: int, confidence: float = 0.9) -> Note:
    return Note(
        start=start,
        end=end,
        pitch=pitch,
        velocity=0.5,
        confidence=confidence,
        low_confidence=confidence < 0.4,
    )


def test_transcription_work_saves_notes(
    db: Session, tab: Tab, store: dict[str, bytes], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(transcription, "transcribe", lambda path, cond, s: [_note(0.0, 0.5, 64)])

    out = tasks.transcription_work(db, tab, tmp_path)

    assert out.output_key == keys.notes_key(tab)
    assert json.loads(store[keys.notes_key(tab)])[0]["pitch"] == 64


def test_transcription_without_notes_fails(
    db: Session, tab: Tab, store: dict[str, bytes], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(transcription, "transcribe", lambda path, cond, s: [])

    with pytest.raises(StageError) as exc:
        tasks.transcription_work(db, tab, tmp_path)
    assert exc.value.error_code == ErrorCode.TAB_NO_NOTES


def test_missing_stem_file_is_stem_missing(
    db: Session, tab: Tab, store: dict[str, bytes], tmp_path: Path
) -> None:
    store.clear()

    with pytest.raises(StageError) as exc:
        tasks.transcription_work(db, tab, tmp_path)
    assert exc.value.error_code == ErrorCode.STEM_MISSING


def test_rhythm_work_reads_input_wav_with_pad(
    db: Session, tab: Tab, store: dict[str, bytes], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[int, int]] = []

    def beats(path: str, offset_ms: int, duration_ms: int, s: object) -> BeatGrid:
        calls.append((offset_ms, duration_ms))
        return BeatGrid(beats=tuple(i * 0.5 for i in range(9)), downbeats=(0.0, 2.0))

    monkeypatch.setattr(rhythm, "estimate_beats", beats)
    store[keys.notes_key(tab)] = (
        b'[{"start":0.0,"end":0.5,"pitch":64,"velocity":0.5,"confidence":0.9}]'
    )

    tasks.rhythm_work(db, tab, tmp_path)

    assert calls == [(500, 4000)]
    saved = json.loads(store[keys.rhythm_key(tab)])
    assert (saved["tempo_bpm"], saved["tempo_uncertain"], saved["notes"][0]["beat"]) == (
        120.0,
        False,
        0.0,
    )


def test_rhythm_without_beats_fails(
    db: Session, tab: Tab, store: dict[str, bytes], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rhythm, "estimate_beats", lambda *a: BeatGrid(beats=(0.0,)))
    store[keys.notes_key(tab)] = b"[]"

    with pytest.raises(StageError) as exc:
        tasks.rhythm_work(db, tab, tmp_path)
    assert exc.value.error_code == ErrorCode.RHYTHM_FAILED


def test_tab_work_builds_json_musicxml_and_review_reasons(
    db: Session, tab: Tab, store: dict[str, bytes], tmp_path: Path
) -> None:
    pytest.importorskip("music21")
    rhythm_output = {
        "notes": [
            {**_note(0.0, 0.5, 64).model_dump(), "beat": 0.0, "duration": 1.0},
            {**_note(0.5, 1.0, 88, 0.2).model_dump(), "beat": 1.0, "duration": 1.0},
        ],
        "tempo_bpm": 120.0,
        "beats_per_bar": 3,
        "non_4_4": True,
        "tempo_uncertain": False,
    }
    store[keys.rhythm_key(tab)] = json.dumps(rhythm_output).encode()

    out = tasks.tab_work(db, tab, tmp_path)

    saved = json.loads(store[keys.tab_json_key(tab)])
    assert [(n["string"], n["fret"], n["playable"]) for n in saved["notes"]] == [
        (1, 0, True),
        (1, 22, False),
    ]
    assert b"<technical>" in store[keys.musicxml_key(tab)]
    assert out.review_reasons == ["LOW_CONFIDENCE_NOTES", "UNPLAYABLE_POSITIONS", "NON_4_4_METER"]


def test_enqueue_pipeline_starts_from_given_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class FakeChain:
        def __init__(self, *steps: object) -> None:
            seen.append([s.task for s in steps])  # type: ignore[attr-defined]

        def apply_async(self) -> None:
            pass

    monkeypatch.setattr(tasks, "chain", FakeChain)
    tasks.enqueue_pipeline(1, TabStage.RHYTHM)

    assert seen == [["tab.quantize", "tab.build_tab"]]
