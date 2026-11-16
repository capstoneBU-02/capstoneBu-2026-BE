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
