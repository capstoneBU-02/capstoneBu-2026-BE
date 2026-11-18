from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import Segment
from app.domains.segment.schemas import (
    SegmentCreateRequest,
    SegmentResponse,
    SegmentUpdateRequest,
)


def build_segment_response(segment: Segment) -> SegmentResponse:
    """구간 생성 및 수정 API의 공통 응답을 생성한다."""
    return SegmentResponse(
        segment_id=segment.id,
        track_id=segment.track_id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        duration_ms=segment.end_ms - segment.start_ms,
        name=segment.name,
        version=segment.version,
        created_at=segment.created_at,
        updated_at=segment.updated_at,
    )


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

        result = build_segment_response(segment)

        db.commit()
        return result

    except SQLAlchemyError as exc:
        db.rollback()
        raise BusinessException(
            ErrorCode.SEGMENT_CREATE_FAILED,
        ) from exc


def update_segment(
    db: Session,
    segment_id: int,
    request: SegmentUpdateRequest,
    track_duration_ms: int,
) -> SegmentResponse:
    """구간 범위를 수정하고 실제 변경 시에만 버전을 증가시킨다."""
    try:
        segment = db.execute(
            select(Segment).where(Segment.id == segment_id).with_for_update()
        ).scalar_one_or_none()

        if segment is None:
            raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)

        validate_segment_range(
            request.start_ms,
            request.end_ms,
            track_duration_ms,
        )

        if segment.start_ms != request.start_ms or segment.end_ms != request.end_ms:
            segment.start_ms = request.start_ms
            segment.end_ms = request.end_ms
            segment.version += 1

        db.flush()
        db.refresh(segment)

        result = build_segment_response(segment)

        db.commit()
        return result

    except SQLAlchemyError as exc:
        db.rollback()
        raise BusinessException(
            ErrorCode.SEGMENT_UPDATE_FAILED,
        ) from exc

    except BusinessException:
        db.rollback()
        raise
