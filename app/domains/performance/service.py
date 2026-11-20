import hashlib
import logging
import uuid
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.input.models import Track
from app.domains.performance.models import Performance, PerformanceHistory
from app.domains.performance.schemas import (
    PerformanceCreateResponse,
    PerformanceListItem,
    PerformanceListResponse,
)
from app.domains.segment.models import Segment

logger = logging.getLogger(__name__)

SOURCE_TYPES = {"RECORDING", "UPLOAD"}

AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def validate_audio_file(
    file: UploadFile,
    file_bytes: bytes,
    source: str,
) -> tuple[str, str]:
    """연주 입력 방식, 파일 크기 및 오디오 형식을 검증한다."""
    if source not in SOURCE_TYPES:
        raise BusinessException(ErrorCode.INVALID_INPUT, "지원하지 않는 연주 입력 방식입니다.")

    if not file_bytes:
        raise BusinessException(ErrorCode.INVALID_INPUT, "빈 음원 파일은 등록할 수 없습니다.")

    settings = get_settings()
    if len(file_bytes) > settings.upload_max_bytes:
        raise BusinessException(ErrorCode.FILE_TOO_LARGE)

    filename = file.filename or "performance"
    ext = Path(filename).suffix.lower()

    if not ext and file.content_type:
        ext = next(
            (
                extension
                for extension, media_type in AUDIO_TYPES.items()
                if media_type == file.content_type.lower()
            ),
            "",
        )

    if ext not in AUDIO_TYPES:
        raise BusinessException(ErrorCode.INVALID_INPUT, "지원하지 않는 음원 파일입니다.")

    content_type = AUDIO_TYPES[ext]
    return ext, content_type


def get_segment_for_workspace(
    db: Session,
    workspace_id: int,
    segment_id: int,
) -> tuple[Segment, Track]:
    """워크스페이스 소유의 구간과 원곡을 함께 조회한다."""
    result = db.execute(
        select(Segment, Track)
        .join(Track, Segment.track_id == Track.id)
        .where(
            Segment.id == segment_id,
            Track.workspace_id == workspace_id,
        )
    ).first()

    if result is None:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)

    return result[0], result[1]


def get_previous_performance(
    db: Session,
    workspace_id: int,
    track_id: int,
    segment_id: int,
) -> Performance | None:
    """동일 워크스페이스·원곡·구간의 가장 최근 연주를 조회한다."""
    return db.scalars(
        select(Performance)
        .where(
            Performance.workspace_id == workspace_id,
            Performance.track_id == track_id,
            Performance.segment_id == segment_id,
        )
        .order_by(
            Performance.created_at.desc(),
            Performance.id.desc(),
        )
        .limit(1)
    ).first()


def delete_storage_object(storage_key: str) -> None:
    """DB 등록 실패 시 업로드한 음원 파일을 정리한다."""
    try:
        settings = get_settings()
        storage.get_s3().delete_object(
            Bucket=settings.s3_bucket,
            Key=storage_key,
        )
    except Exception:
        logger.exception("연주 파일 정리 실패")


def create_performance(
    db: Session,
    workspace_id: int,
    segment_id: int,
    source: str,
    audio_file: UploadFile,
) -> PerformanceCreateResponse:
    """연주 파일을 저장하고 이전 연주와의 이력을 연결한다."""
    file_bytes = audio_file.file.read()

    ext, content_type = validate_audio_file(audio_file, file_bytes, source)

    segment, track = get_segment_for_workspace(
        db,
        workspace_id,
        segment_id,
    )

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    storage_key = f"{workspace_id}/performances/{uuid.uuid4().hex}{ext}"

    try:
        storage.put_bytes(storage_key, file_bytes, content_type)

        previous = get_previous_performance(
            db,
            workspace_id,
            track.id,
            segment.id,
        )

        performance = Performance(
            workspace_id=workspace_id,
            track_id=track.id,
            segment_id=segment.id,
            source=source,
            storage_key=storage_key,
            file_hash=file_hash,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
        )

        db.add(performance)
        db.flush()

        if previous is not None:
            db.add(
                PerformanceHistory(
                    performance_id=performance.id,
                    previous_performance_id=previous.id,
                )
            )

        db.refresh(performance)

        response = PerformanceCreateResponse(
            performance_id=performance.id,
            workspace_id=performance.workspace_id,
            track_id=performance.track_id,
            segment_id=performance.segment_id,
            source=performance.source,
            storage_key=performance.storage_key,
            start_ms=performance.start_ms,
            end_ms=performance.end_ms,
            created_at=performance.created_at,
        )

        db.commit()
        return response

    except SQLAlchemyError as exc:
        db.rollback()
        delete_storage_object(storage_key)
        logger.exception("연주 데이터 DB 저장 실패")
        raise BusinessException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            "연주 데이터 저장에 실패했습니다.",
        ) from exc

    except ClientError as exc:
        db.rollback()
        logger.exception("연주 파일 Object Storage 저장 실패")
        raise BusinessException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            "연주 파일 저장에 실패했습니다.",
        ) from exc


def get_performances_by_segment(
    db: Session,
    workspace_id: int,
    segment_id: int,
) -> PerformanceListResponse:
    """워크스페이스 소유 구간의 연주 목록을 최신순으로 조회한다."""
    get_segment_for_workspace(db, workspace_id, segment_id)

    performances = db.scalars(
        select(Performance)
        .where(
            Performance.workspace_id == workspace_id,
            Performance.segment_id == segment_id,
        )
        .order_by(
            Performance.created_at.desc(),
            Performance.id.desc(),
        )
    ).all()

    return PerformanceListResponse(
        segment_id=segment_id,
        performances=[
            PerformanceListItem(
                performance_id=item.id,
                track_id=item.track_id,
                source=item.source,
                storage_key=item.storage_key,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                created_at=item.created_at,
            )
            for item in performances
        ],
    )
