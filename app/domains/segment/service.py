from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import Segment
from app.domains.segment.schemas import SegmentCreateRequest, SegmentResponse


def validate_segment_range(
    start_ms: int,
    end_ms: int,
    track_duration_ms: int,
) -> None:
    """구간이 원곡 길이 안에 있는지 확인한다."""
    if not 0 <= start_ms < end_ms <= track_duration_ms:
        raise BusinessException(
            ErrorCode.INVALID_SEGMENT_RANGE,
        )


def create_segment(
    db: Session,
    track_id: int,
    request: SegmentCreateRequest,
    track_duration_ms: int,
) -> SegmentResponse:
    """검증된 트랙에 새 구간을 저장한다."""
    validate_segment_range(
        request.start_ms,
        request.end_ms,
        track_duration_ms,
    )

    segment = Segment(
        track_id=track_id,
        start_ms=request.start_ms,
        end_ms=request.end_ms,
        name=None,
        version=1,
    )

    try:
        db.add(segment)
        db.flush()
        db.refresh(segment)

        result = SegmentResponse(
            segment_id=segment.id,
            track_id=segment.track_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            duration_ms=segment.end_ms - segment.start_ms,
            name=segment.name,
            version=segment.version,
            created_at=segment.created_at,
        )

        db.commit()
        return result

    except SQLAlchemyError as exc:
        db.rollback()
        raise BusinessException(
            ErrorCode.SEGMENT_CREATE_FAILED,
        ) from exc
