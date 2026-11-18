from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import SegmentHistory
from app.domains.segment.schemas import (
    SegmentCreateRequest,
    SegmentUpdateRequest,
)
from app.domains.segment.service import (
    create_segment,
    update_segment,
    validate_segment_range,
)


def make_segment(
    start_ms: int = 1000,
    end_ms: int = 5000,
    name: str | None = None,
    version: int = 1,
) -> Mock:
    """구간 수정 테스트에 사용할 가짜 구간을 생성한다."""
    segment = Mock()
    segment.id = 10
    segment.track_id = 1
    segment.start_ms = start_ms
    segment.end_ms = end_ms
    segment.name = name
    segment.version = version
    segment.created_at = datetime(2026, 11, 18, tzinfo=UTC)
    segment.updated_at = datetime(2026, 11, 18, tzinfo=UTC)
    return segment


def make_db(segment: Mock) -> Mock:
    """구간 조회 결과를 반환하는 가짜 DB 세션을 생성한다."""
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = segment
    return db


def get_saved_history(db: Mock) -> SegmentHistory:
    """DB 세션에 추가된 변경 이력을 반환한다."""
    db.add.assert_called_once()
    history = db.add.call_args.args[0]
    assert isinstance(history, SegmentHistory)
    return history


def test_valid_segment_range() -> None:
    validate_segment_range(
        start_ms=10500,
        end_ms=25800,
        track_duration_ms=60000,
    )


def test_segment_can_start_at_zero() -> None:
    validate_segment_range(
        start_ms=0,
        end_ms=10000,
        track_duration_ms=60000,
    )


def test_segment_can_end_at_track_duration() -> None:
    validate_segment_range(
        start_ms=50000,
        end_ms=60000,
        track_duration_ms=60000,
    )


@pytest.mark.parametrize(
    ("start_ms", "end_ms", "duration_ms"),
    [
        (-1, 10000, 60000),
        (10000, 10000, 60000),
        (20000, 10000, 60000),
        (10000, 70000, 60000),
    ],
)
def test_invalid_segment_range(
    start_ms: int,
    end_ms: int,
    duration_ms: int,
) -> None:
    with pytest.raises(BusinessException) as exc_info:
        validate_segment_range(
            start_ms=start_ms,
            end_ms=end_ms,
            track_duration_ms=duration_ms,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_SEGMENT_RANGE


def test_create_segment_rolls_back_on_db_error() -> None:
    db = Mock()
    db.flush.side_effect = OperationalError(
        "INSERT INTO segments",
        {},
        Exception("database unavailable"),
    )

    request = SegmentCreateRequest(
        start_ms=10500,
        end_ms=25800,
    )

    with pytest.raises(BusinessException) as exc_info:
        create_segment(
            db=db,
            track_id=1,
            request=request,
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.SEGMENT_CREATE_FAILED
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_update_segment_increments_version_when_range_changes() -> None:
    segment = make_segment()
    db = make_db(segment)

    request = SegmentUpdateRequest(
        start_ms=2000,
        end_ms=6000,
    )

    result = update_segment(
        db=db,
        segment_id=10,
        request=request,
        track_duration_ms=60000,
    )

    assert result.start_ms == 2000
    assert result.end_ms == 6000
    assert result.version == 2
    db.commit.assert_called_once()


def test_update_segment_keeps_version_when_range_unchanged() -> None:
    segment = make_segment(version=3)
    db = make_db(segment)

    request = SegmentUpdateRequest(
        start_ms=1000,
        end_ms=5000,
    )

    result = update_segment(
        db=db,
        segment_id=10,
        request=request,
        track_duration_ms=60000,
    )

    assert result.start_ms == 1000
    assert result.end_ms == 5000
    assert result.version == 3
    db.commit.assert_called_once()


def test_update_segment_uses_for_update_lock() -> None:
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    request = SegmentUpdateRequest(
        start_ms=1000,
        end_ms=5000,
    )

    with pytest.raises(BusinessException) as exc_info:
        update_segment(
            db=db,
            segment_id=10,
            request=request,
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.SEGMENT_NOT_FOUND

    query = db.execute.call_args.args[0]
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "FOR UPDATE" in sql
    db.rollback.assert_called_once()


def test_update_segment_rolls_back_on_db_error() -> None:
    db = Mock()
    db.execute.side_effect = OperationalError(
        "SELECT * FROM segments FOR UPDATE",
        {},
        Exception("database unavailable"),
    )

    request = SegmentUpdateRequest(
        start_ms=2000,
        end_ms=6000,
    )

    with pytest.raises(BusinessException) as exc_info:
        update_segment(
            db=db,
            segment_id=10,
            request=request,
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.SEGMENT_UPDATE_FAILED
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_update_name_saves_history_without_version_increment() -> None:
    """이름만 변경하면 변경 전 상태를 저장하고 버전은 유지한다."""
    segment = make_segment(name=None, version=1)
    db = make_db(segment)

    result = update_segment(
        db=db,
        segment_id=10,
        request=SegmentUpdateRequest(name="VERSE"),
        track_duration_ms=60000,
    )

    history = get_saved_history(db)

    assert history.segment_id == 10
    assert history.start_ms == 1000
    assert history.end_ms == 5000
    assert history.name is None
    assert history.version == 1

    assert result.name == "VERSE"
    assert result.version == 1
    db.commit.assert_called_once()


def test_update_range_saves_previous_values() -> None:
    """범위 변경 시 이전 시간 범위와 버전을 보관한다."""
    segment = make_segment(
        start_ms=1000,
        end_ms=5000,
        name="VERSE",
        version=2,
    )
    db = make_db(segment)

    result = update_segment(
        db=db,
        segment_id=10,
        request=SegmentUpdateRequest(
            start_ms=2000,
            end_ms=6000,
        ),
        track_duration_ms=60000,
    )

    history = get_saved_history(db)

    assert history.start_ms == 1000
    assert history.end_ms == 5000
    assert history.name == "VERSE"
    assert history.version == 2

    assert result.start_ms == 2000
    assert result.end_ms == 6000
    assert result.version == 3
    db.commit.assert_called_once()


def test_update_name_and_range_saves_one_history() -> None:
    """이름과 범위를 동시에 수정해도 이력은 한 건만 생성한다."""
    segment = make_segment(name="VERSE", version=1)
    db = make_db(segment)

    result = update_segment(
        db=db,
        segment_id=10,
        request=SegmentUpdateRequest(
            start_ms=3000,
            end_ms=7000,
            name="CHORUS",
        ),
        track_duration_ms=60000,
    )

    history = get_saved_history(db)

    assert history.start_ms == 1000
    assert history.end_ms == 5000
    assert history.name == "VERSE"
    assert history.version == 1

    assert result.start_ms == 3000
    assert result.end_ms == 7000
    assert result.name == "CHORUS"
    assert result.version == 2
    db.commit.assert_called_once()


def test_update_same_values_does_not_save_history() -> None:
    """변경 사항이 없으면 새로운 이력을 생성하지 않는다."""
    segment = make_segment(name="VERSE", version=3)
    db = make_db(segment)

    result = update_segment(
        db=db,
        segment_id=10,
        request=SegmentUpdateRequest(
            start_ms=1000,
            end_ms=5000,
            name="VERSE",
        ),
        track_duration_ms=60000,
    )

    assert result.version == 3
    assert result.name == "VERSE"
    db.add.assert_not_called()
    db.commit.assert_called_once()


def test_invalid_range_does_not_save_history() -> None:
    """유효하지 않은 범위 요청은 이력이나 변경을 남기지 않는다."""
    segment = make_segment(name="VERSE", version=1)
    db = make_db(segment)

    with pytest.raises(BusinessException) as exc_info:
        update_segment(
            db=db,
            segment_id=10,
            request=SegmentUpdateRequest(
                start_ms=7000,
                end_ms=3000,
            ),
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_SEGMENT_RANGE
    assert segment.start_ms == 1000
    assert segment.end_ms == 5000
    assert segment.version == 1
    db.add.assert_not_called()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
