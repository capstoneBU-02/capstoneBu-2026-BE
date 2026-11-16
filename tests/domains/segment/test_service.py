from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import Segment
from app.domains.segment.schemas import SegmentUpdateRequest
from app.domains.segment.service import update_segment, validate_segment_range


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


def test_update_segment_preserves_id_and_increments_version() -> None:
    db = Mock()

    segment = Segment(
        id=10,
        track_id=1,
        start_ms=5000,
        end_ms=15000,
        name=None,
        version=1,
    )
    segment.created_at = datetime(2026, 11, 16, tzinfo=UTC)

    db.query.return_value.filter.return_value.first.return_value = segment

    result = update_segment(
        db=db,
        track_id=1,
        segment_id=10,
        request=SegmentUpdateRequest(
            start_ms=7000,
            end_ms=17000,
        ),
        track_duration_ms=60000,
    )

    assert result.segment_id == 10
    assert result.start_ms == 7000
    assert result.end_ms == 17000
    assert result.version == 2
    assert result.duration_ms == 10000

    db.commit.assert_called_once()


def test_update_segment_rejects_invalid_range() -> None:
    db = Mock()

    segment = Segment(
        id=10,
        track_id=1,
        start_ms=5000,
        end_ms=15000,
        version=1,
    )

    db.query.return_value.filter.return_value.first.return_value = segment

    with pytest.raises(BusinessException) as exc_info:
        update_segment(
            db=db,
            track_id=1,
            segment_id=10,
            request=SegmentUpdateRequest(
                start_ms=20000,
                end_ms=10000,
            ),
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_SEGMENT_RANGE
    assert segment.version == 1
    db.commit.assert_not_called()


def test_update_segment_rejects_missing_segment() -> None:
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(BusinessException) as exc_info:
        update_segment(
            db=db,
            track_id=1,
            segment_id=999,
            request=SegmentUpdateRequest(
                start_ms=7000,
                end_ms=17000,
            ),
            track_duration_ms=60000,
        )

    assert exc_info.value.error_code == ErrorCode.SEGMENT_NOT_FOUND
    db.commit.assert_not_called()
