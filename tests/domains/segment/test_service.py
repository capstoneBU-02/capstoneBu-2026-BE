import pytest

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.service import validate_segment_range


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
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    from app.core.exceptions import BusinessException, ErrorCode
    from app.domains.segment.schemas import SegmentCreateRequest
    from app.domains.segment.service import create_segment

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
    from datetime import UTC, datetime
    from unittest.mock import Mock

    from app.domains.segment.schemas import SegmentUpdateRequest
    from app.domains.segment.service import update_segment

    segment = Mock()
    segment.id = 10
    segment.track_id = 1
    segment.start_ms = 1000
    segment.end_ms = 5000
    segment.name = None
    segment.version = 1
    segment.created_at = datetime(2026, 11, 18, tzinfo=UTC)
    segment.updated_at = datetime(2026, 11, 18, tzinfo=UTC)

    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = segment

    request = SegmentUpdateRequest(start_ms=2000, end_ms=6000)

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
    from datetime import UTC, datetime
    from unittest.mock import Mock

    from app.domains.segment.schemas import SegmentUpdateRequest
    from app.domains.segment.service import update_segment

    segment = Mock()
    segment.id = 10
    segment.track_id = 1
    segment.start_ms = 1000
    segment.end_ms = 5000
    segment.name = None
    segment.version = 3
    segment.created_at = datetime(2026, 11, 18, tzinfo=UTC)
    segment.updated_at = datetime(2026, 11, 18, tzinfo=UTC)

    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = segment

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
    from unittest.mock import Mock

    from sqlalchemy.dialects import postgresql

    from app.core.exceptions import BusinessException, ErrorCode
    from app.domains.segment.schemas import SegmentUpdateRequest
    from app.domains.segment.service import update_segment

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
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    from app.domains.segment.schemas import SegmentUpdateRequest
    from app.domains.segment.service import update_segment

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
