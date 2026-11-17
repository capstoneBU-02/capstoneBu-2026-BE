import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.processing.models import Stem, StemStatus
from app.domains.tab.models import ReviewReason, Tab, TabStageResult, TabStatus
from tests.domains.conftest import FakeSegments


def make_stem(db: Session, segments: FakeSegments) -> Stem:
    source = segments.add(segment_id=100)
    stem = Stem(
        workspace_id=source.workspace_id,
        track_id=source.track_id,
        segment_id=source.segment_id,
        start_ms=0,
        end_ms=10_000,
        version=1,
        status=StemStatus.SUCCESS,
        bypass_separation=False,
        input_hash="s" * 64,
        config_version="2026-10-01",
    )
    db.add(stem)
    db.flush()
    return stem


def make_tab(stem: Stem, version: int = 1, **fields: object) -> Tab:
    values: dict[str, object] = {
        "workspace_id": stem.workspace_id,
        "track_id": stem.track_id,
        "segment_id": stem.segment_id,
        "stem_id": stem.id,
        "version": version,
        "input_hash": f"{version}" * 64,
        "config_version": "2026-10-01",
    }
    return Tab(**(values | fields))


def test_tab_defaults(db: Session, segments: FakeSegments) -> None:
    tab = make_tab(make_stem(db, segments))
    db.add(tab)
    db.commit()
    db.refresh(tab)

    assert (tab.result_type, tab.tuning_id, tab.capo) == ("original", "e_standard", 0)
    assert tab.status == TabStatus.QUEUED
    assert tab.review_reasons == []
    assert tab.retry_count == 0


def test_review_reasons_round_trip(db: Session, segments: FakeSegments) -> None:
    reasons = [ReviewReason.LOW_CONFIDENCE_NOTES, ReviewReason.UNPLAYABLE_POSITIONS]
    tab = make_tab(make_stem(db, segments), review_reasons=list(reasons))
    db.add(tab)
    db.commit()
    db.expire_all()

    assert db.get(Tab, tab.id).review_reasons == ["LOW_CONFIDENCE_NOTES", "UNPLAYABLE_POSITIONS"]


def test_same_segment_version_is_rejected(db: Session, segments: FakeSegments) -> None:
    stem = make_stem(db, segments)
    db.add_all([make_tab(stem, 1), make_tab(stem, 1, input_hash="x" * 64)])

    with pytest.raises(IntegrityError):
        db.commit()


def test_only_one_active_tab_per_input(db: Session, segments: FakeSegments) -> None:
    stem = make_stem(db, segments)
    db.add_all([make_tab(stem, 1, input_hash="h" * 64), make_tab(stem, 2, input_hash="h" * 64)])

    with pytest.raises(IntegrityError):
        db.commit()


def test_finished_tabs_can_share_input(db: Session, segments: FakeSegments) -> None:
    stem = make_stem(db, segments)
    db.add_all(
        [
            make_tab(stem, 1, input_hash="h" * 64, status=TabStatus.FAILED),
            make_tab(stem, 2, input_hash="h" * 64),
        ]
    )

    db.commit()


def test_tab_requires_existing_stem(db: Session, segments: FakeSegments) -> None:
    stem = make_stem(db, segments)
    db.add(make_tab(stem, stem_id=stem.id + 999))

    with pytest.raises(IntegrityError):
        db.commit()


def test_negative_capo_is_rejected(db: Session, segments: FakeSegments) -> None:
    db.add(make_tab(make_stem(db, segments), capo=-1))

    with pytest.raises(IntegrityError):
        db.commit()


def test_stage_results_are_deleted_with_tab(db: Session, segments: FakeSegments) -> None:
    tab = make_tab(make_stem(db, segments))
    db.add(tab)
    db.flush()
    db.add(TabStageResult(tab_id=tab.id, stage="transcription", status="success", attempt=1))
    db.commit()

    db.delete(tab)
    db.commit()

    assert db.query(TabStageResult).count() == 0
