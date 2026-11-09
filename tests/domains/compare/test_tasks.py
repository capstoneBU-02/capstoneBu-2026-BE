from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.compare import keys, service, tasks
from app.domains.compare.models import (
    Comparison,
    ComparisonRegion,
    ComparisonStage,
    ComparisonStageResult,
)
from app.domains.compare.pipeline import contract, judge
from app.domains.processing.models import Stem
from tests.domains.compare.conftest import WS, FakeStorage
from tests.domains.conftest import EnqueueSpy

ALIGN = contract.AlignmentResult(path=[(0, 0), (4000, 4400)], slope=1.1, is_slow=True)
NOTES = contract.NotesResult(
    reference=[contract.Note(0, 500, 64, 0.9)], performance=[contract.Note(30, 520, 64, 0.8)]
)
REGION = contract.Region("timing_difference", 1000, 1500, 1100, 1650)


@pytest.fixture
def comparison(
    db: Session, ready: Stem, enqueue: EnqueueSpy, fake_storage: FakeStorage
) -> Comparison:
    fake_storage.files[ready.storage_key or ""] = b"RIFFref"
    fake_storage.files["10/performances/500.wav"] = b"RIFFperf"
    return service.request_comparison(db, WS, 500, enqueue=enqueue).comparison


@pytest.fixture
def fake_judge(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """판정 함수 대역. 값을 바꿔 다음 호출 결과를 정한다."""
    out: dict[str, object] = {
        "align": ALIGN,
        "notes": NOTES,
        "judge": contract.JudgeResult([REGION]),
    }
    monkeypatch.setattr(judge, "align", lambda r, p, s: out["align"])
    monkeypatch.setattr(judge, "transcribe_both", lambda r, p, s: out["notes"])
    monkeypatch.setattr(judge, "judge", lambda a, n, s: out["judge"])
    return out


def _run_all(c: Comparison, factory: Callable[[], Session]) -> list[bool]:
    return [tasks.run_stage(c.id, s, tasks._WORK[s], factory) for s in ComparisonStage]


def _stages(db: Session, cid: int) -> list[tuple[str, str]]:
    rows = db.scalars(
        select(ComparisonStageResult)
        .where(ComparisonStageResult.comparison_id == cid)
        .order_by(ComparisonStageResult.id)
    ).all()
    return [(r.stage, r.status) for r in rows]


def _reload(db: Session, c: Comparison) -> Comparison:
    db.expire_all()
    got = db.get(Comparison, c.id)
    assert got is not None
    return got


def test_full_pipeline_saves_regions_and_result(
    db: Session,
    comparison: Comparison,
    fake_judge: dict[str, object],
    fake_storage: FakeStorage,
    session_factory: Callable[[], Session],
) -> None:
    assert _run_all(comparison, session_factory) == [True, True, True]
    c = _reload(db, comparison)
    assert (c.status, c.result_key) == ("success", keys.result_key(c))
    assert keys.alignment_key(c) in fake_storage.files and keys.notes_key(c) in fake_storage.files
    regions = db.scalars(
        select(ComparisonRegion).where(ComparisonRegion.comparison_id == c.id)
    ).all()
    assert [(r.type, r.start_ms, r.performance_end_ms) for r in regions] == [
        ("timing_difference", 1000, 1650)
    ]

    res = service.get_result(db, WS, c.id)
    assert res.overall_tempo is not None and res.overall_tempo.slope == 1.1


def test_rerecord_at_align_ends_not_comparable(
    db: Session,
    comparison: Comparison,
    fake_judge: dict[str, object],
    session_factory: Callable[[], Session],
) -> None:
    fake_judge["align"] = contract.AlignmentResult([], 1.0, False, "RERECORD_TOO_SHORT")
    assert _run_all(comparison, session_factory) == [False, False, False]
    c = _reload(db, comparison)
    assert (c.status, c.not_comparable_reason) == ("not_comparable", "RERECORD_TOO_SHORT")
    assert _stages(db, c.id) == [
        ("align", "success"),
        ("transcription", "skipped"),
        ("compare", "skipped"),
    ]
    assert service.get_result(db, WS, c.id).regions == []


def test_not_comparable_from_judge(
    db: Session,
    comparison: Comparison,
    fake_judge: dict[str, object],
    session_factory: Callable[[], Session],
) -> None:
    fake_judge["judge"] = contract.JudgeResult([], "LOW_MATCH_RATE")
    _run_all(comparison, session_factory)
    c = _reload(db, comparison)
    assert (c.status, c.not_comparable_reason) == ("not_comparable", "LOW_MATCH_RATE")


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (contract.InvalidAudioError("silent"), "PERFORMANCE_INVALID_AUDIO"),
        (NotImplementedError("judge 미구현"), "AI_WORKER_ERROR"),
    ],
)
def test_align_failure_codes(
    db: Session,
    comparison: Comparison,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: Callable[[], Session],
    exc: Exception,
    code: str,
) -> None:
    def boom(*a: object) -> contract.AlignmentResult:
        raise exc

    monkeypatch.setattr(judge, "align", boom)
    assert not tasks.run_stage(
        comparison.id, ComparisonStage.ALIGN, tasks.align_work, session_factory
    )
    c = _reload(db, comparison)
    assert (c.status, c.error_code, c.current_stage) == ("failed", code, "align")


def test_missing_stem_file_is_stem_missing(
    db: Session,
    comparison: Comparison,
    fake_judge: dict[str, object],
    fake_storage: FakeStorage,
    ready: Stem,
    session_factory: Callable[[], Session],
) -> None:
    del fake_storage.files[ready.storage_key or ""]
    tasks.run_stage(comparison.id, ComparisonStage.ALIGN, tasks.align_work, session_factory)
    assert _reload(db, comparison).error_code == "STEM_MISSING"


def test_rerun_compare_does_not_duplicate_regions(db: Session, comparison: Comparison) -> None:
    tasks._save_regions(db, comparison, [REGION, REGION])
    tasks._save_regions(db, comparison, [REGION])
    db.commit()
    count = db.scalars(
        select(ComparisonRegion).where(ComparisonRegion.comparison_id == comparison.id)
    ).all()
    assert len(count) == 1


def test_enqueue_pipeline_starts_from_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[str]] = []

    class FakeChain:
        def __init__(self, *steps: object) -> None:
            sent.append([s.task for s in steps])  # type: ignore[attr-defined]

        def apply_async(self) -> None:
            pass

    monkeypatch.setattr(tasks, "chain", FakeChain)
    tasks.enqueue_pipeline(7, ComparisonStage.TRANSCRIPTION)
    assert sent == [["compare.transcribe", "compare.compare"]]
