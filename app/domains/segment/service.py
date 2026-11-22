from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import Segment, SegmentHistory
from app.domains.segment.schemas import (
    SegmentCreateRequest,
    SegmentListResponse,
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
        raise BusinessException(ErrorCode.INVALID_SEGMENT_RANGE)


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
        name=request.name,
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
        raise BusinessException(ErrorCode.SEGMENT_CREATE_FAILED) from exc


def save_segment_history(
    db: Session,
    segment: Segment,
) -> None:
    """구간 변경 전 정보를 이력으로 저장한다."""
    history = SegmentHistory(
        segment_id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        name=segment.name,
        version=segment.version,
    )
    db.add(history)


def apply_segment_update(
    segment: Segment,
    request: SegmentUpdateRequest,
    track_duration_ms: int,
) -> bool:
    """변경 요청을 적용하고 실제 변경 여부를 반환한다."""
    fields = request.model_fields_set
    changed = False

    if "start_ms" in fields:
        start_ms = request.start_ms
        end_ms = request.end_ms

        if start_ms is None or end_ms is None:
            raise BusinessException(ErrorCode.INVALID_SEGMENT_RANGE)

        validate_segment_range(
            start_ms,
            end_ms,
            track_duration_ms,
        )

        if segment.start_ms != start_ms or segment.end_ms != end_ms:
            segment.start_ms = start_ms
            segment.end_ms = end_ms
            segment.version += 1
            changed = True

    if "name" in fields and segment.name != request.name:
        segment.name = request.name
        changed = True

    return changed


def update_segment(
    db: Session,
    segment_id: int,
    request: SegmentUpdateRequest,
    track_duration_ms: int,
) -> SegmentResponse:
    """구간을 수정하고 변경 전 정보를 이력으로 저장한다."""
    try:
        segment = db.execute(
            select(Segment).where(Segment.id == segment_id).with_for_update()
        ).scalar_one_or_none()

        if segment is None:
            raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)

        old_start_ms = segment.start_ms
        old_end_ms = segment.end_ms
        old_name = segment.name

        fields = request.model_fields_set

        if "start_ms" in fields:
            start_ms = request.start_ms
            end_ms = request.end_ms

            if start_ms is None or end_ms is None:
                raise BusinessException(ErrorCode.INVALID_SEGMENT_RANGE)

            validate_segment_range(
                start_ms,
                end_ms,
                track_duration_ms,
            )

        range_changed = False

        if "start_ms" in fields:
            range_changed = old_start_ms != request.start_ms or old_end_ms != request.end_ms

        name_changed = "name" in fields and old_name != request.name

        if range_changed or name_changed:
            save_segment_history(db, segment)

            apply_segment_update(
                segment,
                request,
                track_duration_ms,
            )

        db.flush()
        db.refresh(segment)

        result = build_segment_response(segment)
        db.commit()
        return result

    except SQLAlchemyError as exc:
        db.rollback()
        raise BusinessException(ErrorCode.SEGMENT_UPDATE_FAILED) from exc

    except BusinessException:
        db.rollback()
        raise


def list_segments(db: Session, track_id: int) -> SegmentListResponse:
    """원곡의 구간 목록. 시작 시간순 (같으면 만든 순서). 소유권은 라우터에서 확인한다."""
    segments = db.scalars(
        select(Segment).where(Segment.track_id == track_id).order_by(Segment.start_ms, Segment.id)
    ).all()
    return SegmentListResponse(items=[build_segment_response(s) for s in segments])
