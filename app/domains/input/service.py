import hashlib
import io
import uuid
from pathlib import Path

from fastapi import UploadFile
from mutagen import File as MutagenFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.input.models import Track
from app.domains.input.schemas import TrackListResponse, TrackResponse, TrackUploadResponse
from app.domains.segment.models import Segment

ALLOWED_EXTENSIONS = {".mp3", ".mp4"}
ALLOWED_MIME_TYPES = {"audio/mpeg", "audio/mp3", "video/mp4", "audio/mp4"}


def validate_file_format(filename: str, content_type: str | None) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    if content_type and content_type.lower() not in ALLOWED_MIME_TYPES:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    return "audio/mpeg" if ext == ".mp3" else "video/mp4"


def extract_duration_ms(file_bytes: bytes, filename: str) -> int:
    try:
        audio = MutagenFile(io.BytesIO(file_bytes), filename=filename)
    except Exception as exc:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT) from exc
    if audio is None or audio.info is None:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    length_sec = getattr(audio.info, "length", None)
    if length_sec is None:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    duration_ms = int(round(float(length_sec) * 1000))
    if duration_ms <= 0:
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    return duration_ms


def upload_track(
    db: Session,
    workspace_id: int,
    file: UploadFile,
    is_separated_stem: bool = False,
) -> TrackUploadResponse:
    settings = get_settings()
    filename = file.filename or "unknown"
    media_type = validate_file_format(filename, file.content_type)

    # 메모리에 읽기 전 file.size로 단일 파일 최대 크기(413) 사전 검증
    if file.size is not None and file.size > settings.upload_max_bytes:
        raise BusinessException(ErrorCode.FILE_TOO_LARGE)

    file_bytes = file.file.read()
    if not file_bytes:
        raise BusinessException(ErrorCode.INVALID_INPUT)
    if len(file_bytes) > settings.upload_max_bytes:
        raise BusinessException(ErrorCode.FILE_TOO_LARGE)

    # 워크스페이스 저장 한도(트랙 수 및 누적 용량) 검증
    total_count, total_bytes = db.execute(
        select(
            func.count(Track.id),
            func.coalesce(func.sum(Track.file_size_bytes), 0),
        ).where(Track.workspace_id == workspace_id)
    ).one()
    if (
        int(total_count) >= settings.workspace_max_tracks
        or int(total_bytes) + len(file_bytes) > settings.workspace_max_storage_bytes
    ):
        raise BusinessException(ErrorCode.STORAGE_LIMIT_EXCEEDED)

    # 오디오 메타데이터 추출 및 config 기반 최대 길이(5분) 검증
    duration_ms = extract_duration_ms(file_bytes, filename)
    if duration_ms > settings.track_max_duration_ms:
        raise BusinessException(ErrorCode.DURATION_EXCEEDED)

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    ext = Path(filename).suffix.lower()
    storage_key = f"{workspace_id}/tracks/{uuid.uuid4().hex}{ext}"

    storage.put_bytes(storage_key, file_bytes, media_type)

    track = Track(
        workspace_id=workspace_id,
        filename=filename,
        media_type=media_type,
        storage_key=storage_key,
        file_hash=file_hash,
        duration_ms=duration_ms,
        file_size_bytes=len(file_bytes),
        is_separated_stem=is_separated_stem,
    )
    db.add(track)
    db.commit()
    db.refresh(track)

    return TrackUploadResponse(
        track_id=track.id,
        filename=track.filename,
        duration_ms=track.duration_ms,
        media_type=track.media_type,
        is_separated_stem=track.is_separated_stem,
        created_at=track.created_at,
    )


def get_owned_track(db: Session, workspace_id: int, track_id: int) -> Track:
    stmt = select(Track).where(Track.id == track_id, Track.workspace_id == workspace_id)
    track = db.scalars(stmt).first()
    if track is None:
        raise BusinessException(ErrorCode.TRACK_NOT_FOUND)
    return track


def _segment_counts(db: Session, track_ids: list[int]) -> dict[int, int]:
    """원곡별 구간 수."""
    if not track_ids:
        return {}
    rows = db.execute(
        select(Segment.track_id, func.count(Segment.id))
        .where(Segment.track_id.in_(track_ids))
        .group_by(Segment.track_id)
    ).all()
    return {track_id: int(count) for track_id, count in rows}


def _track_response(track: Track, segment_count: int) -> TrackResponse:
    return TrackResponse(
        track_id=track.id,
        filename=track.filename,
        duration_ms=track.duration_ms,
        media_type=track.media_type,
        is_separated_stem=track.is_separated_stem,
        segment_count=segment_count,
        created_at=track.created_at,
    )


def list_tracks(db: Session, workspace_id: int) -> TrackListResponse:
    """내 워크스페이스의 원곡 목록. 최근에 올린 곡부터."""
    tracks = db.scalars(
        select(Track)
        .where(Track.workspace_id == workspace_id)
        .order_by(Track.created_at.desc(), Track.id.desc())
    ).all()
    counts = _segment_counts(db, [t.id for t in tracks])
    return TrackListResponse(items=[_track_response(t, counts.get(t.id, 0)) for t in tracks])


def get_track(db: Session, workspace_id: int, track_id: int) -> TrackResponse:
    """원곡 상세. 없거나 다른 워크스페이스 곡이면 TRACK_NOT_FOUND."""
    track = get_owned_track(db, workspace_id, track_id)
    return _track_response(track, _segment_counts(db, [track.id]).get(track.id, 0))
