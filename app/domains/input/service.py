import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile
from mutagen import File as MutagenFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.input.models import Track
from app.domains.input.schemas import (
    InputSegmentCreateRequest,
    InputSegmentResponse,
    InputStemCreateResponse,
    InputStemStatusResponse,
    TabCreateRequest,
    TabCreateResponse,
    TrackUploadResponse,
)
from app.domains.processing import service as processing_service
from app.domains.processing.models import (
    ACTIVE_STATUSES,
    READY_STATUSES,
    Stem,
    StemStage,
    StemStatus,
)
from app.domains.segment.models import Segment

ALLOWED_EXTENSIONS = {".mp3", ".mp4"}
ALLOWED_MIME_TYPES = {"audio/mpeg", "audio/mp3", "video/mp4", "audio/mp4"}
MAX_DURATION_MS = 300_000  # 5분 (300,000ms)

SUPPORTED_PRIORITIES = {"solo", "riff", "accompaniment"}
SUPPORTED_TUNINGS = {"e_standard"}
SUPPORTED_CAPOS = {0}


@dataclass(frozen=True)
class _InMemoryTabJob:
    tab_id: int
    segment_id: int
    version: int
    status: str
    priority: str
    tuning_id: str
    capo: int
    created_at: datetime


_TAB_JOBS: dict[tuple[int, int], list[_InMemoryTabJob]] = {}
_TAB_ID_SEQ = 0


def reset_tab_jobs() -> None:
    """테스트 격리용 초기화 함수."""
    global _TAB_ID_SEQ
    _TAB_JOBS.clear()
    _TAB_ID_SEQ = 0


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
    if audio is None or audio.info is None or not getattr(audio.info, "length", None):
        raise BusinessException(ErrorCode.UNSUPPORTED_FORMAT)
    return int(round(float(audio.info.length) * 1000))


def upload_track(
    db: Session,
    workspace_id: int,
    file: UploadFile,
    is_separated_stem: bool = False,
) -> TrackUploadResponse:
    settings = get_settings()
    filename = file.filename or "unknown"
    media_type = validate_file_format(filename, file.content_type)

    file_bytes = file.file.read()
    if not file_bytes:
        raise BusinessException(ErrorCode.INVALID_INPUT)

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

    # 오디오 메타데이터 추출 및 최대 300,000ms(5분) 검증
    duration_ms = extract_duration_ms(file_bytes, filename)
    if duration_ms <= 0 or duration_ms > MAX_DURATION_MS:
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


def _get_owned_segment_and_track(
    db: Session, workspace_id: int, segment_id: int
) -> tuple[Segment, Track]:
    segment = db.get(Segment, segment_id)
    if segment is None:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)
    track = db.get(Track, segment.track_id)
    if track is None or track.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)
    return segment, track


def create_segment(
    db: Session,
    workspace_id: int,
    track_id: int,
    payload: InputSegmentCreateRequest,
) -> InputSegmentResponse:
    track = get_owned_track(db, workspace_id, track_id)
    if not 0 <= payload.start_ms < payload.end_ms <= track.duration_ms:
        raise BusinessException(ErrorCode.INVALID_SEGMENT_RANGE)

    segment = Segment(
        track_id=track.id,
        start_ms=payload.start_ms,
        end_ms=payload.end_ms,
        name=payload.name,
        version=1,
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)

    return InputSegmentResponse(
        segment_id=segment.id,
        track_id=segment.track_id,
        name=segment.name,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        duration_ms=segment.end_ms - segment.start_ms,
        version=segment.version,
        created_at=segment.created_at,
    )


def request_stem_separation(
    db: Session,
    workspace_id: int,
    segment_id: int,
) -> tuple[InputStemCreateResponse, bool]:
    settings = get_settings()
    segment, track = _get_owned_segment_and_track(db, workspace_id, segment_id)

    payload = {
        "workspace_id": workspace_id,
        "segment_id": segment.id,
        "file_hash": track.file_hash,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "bypass_separation": track.is_separated_stem,
        "separation_model": None if track.is_separated_stem else settings.ai_separation_model,
        "config_version": settings.ai_config_version,
    }
    input_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    existing = db.scalars(
        select(Stem)
        .where(
            Stem.input_hash == input_hash,
            Stem.deleted_at.is_(None),
            Stem.status.in_([*ACTIVE_STATUSES, *READY_STATUSES]),
        )
        .order_by(Stem.version.desc())
        .limit(1)
    ).first()
    if existing is not None:
        if existing.status in ACTIVE_STATUSES:
            raise BusinessException(ErrorCode.STEM_IN_PROGRESS)
        return (
            InputStemCreateResponse(
                stem_id=existing.id,
                segment_id=existing.segment_id,
                version=existing.version,
                status=existing.status,
                target_part="guitar",
                bypass_separation=existing.bypass_separation,
                is_reused=True,
                created_at=existing.created_at,
            ),
            True,
        )

    last_version = db.scalar(
        select(func.coalesce(func.max(Stem.version), 0)).where(Stem.segment_id == segment.id)
    )
    stem = Stem(
        workspace_id=workspace_id,
        track_id=track.id,
        segment_id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        version=int(last_version or 0) + 1,
        part="guitar",
        status=StemStatus.QUEUED,
        bypass_separation=track.is_separated_stem,
        input_hash=input_hash,
        config_version=settings.ai_config_version,
    )
    db.add(stem)
    db.commit()
    db.refresh(stem)

    # processing 도메인의 기존 작업 등록 함수 재사용
    processing_service._default_enqueue(stem.id, StemStage.PREPROCESS)

    return (
        InputStemCreateResponse(
            stem_id=stem.id,
            segment_id=stem.segment_id,
            version=stem.version,
            status=stem.status,
            target_part="guitar",
            bypass_separation=stem.bypass_separation,
            is_reused=False,
            created_at=stem.created_at,
        ),
        False,
    )


def get_stem_status(
    db: Session,
    workspace_id: int,
    stem_id: int,
) -> InputStemStatusResponse:
    stem = db.scalars(
        select(Stem).where(
            Stem.id == stem_id,
            Stem.workspace_id == workspace_id,
            Stem.deleted_at.is_(None),
        )
    ).first()
    if stem is None:
        raise BusinessException(ErrorCode.STEM_NOT_FOUND)

    return InputStemStatusResponse(
        stem_id=stem.id,
        segment_id=stem.segment_id,
        version=stem.version,
        status=stem.status,
        bypass_separation=stem.bypass_separation,
        updated_at=stem.updated_at,
    )


def request_tab_generation(
    db: Session,
    workspace_id: int,
    segment_id: int,
    payload: TabCreateRequest,
) -> TabCreateResponse:
    if (
        payload.priority not in SUPPORTED_PRIORITIES
        or payload.tuning_id not in SUPPORTED_TUNINGS
        or payload.capo not in SUPPORTED_CAPOS
    ):
        raise BusinessException(ErrorCode.UNSUPPORTED_CONDITION)

    _get_owned_segment_and_track(db, workspace_id, segment_id)

    latest_stem = db.scalars(
        select(Stem)
        .where(Stem.segment_id == segment_id, Stem.deleted_at.is_(None))
        .order_by(Stem.version.desc())
        .limit(1)
    ).first()
    if latest_stem is None or latest_stem.status not in READY_STATUSES:
        raise BusinessException(ErrorCode.STEM_NOT_READY)

    global _TAB_ID_SEQ
    key = (workspace_id, segment_id)
    history = _TAB_JOBS.setdefault(key, [])

    if history:
        latest = history[-1]
        if (
            latest.status != StemStatus.FAILED
            and latest.priority == payload.priority
            and latest.tuning_id == payload.tuning_id
            and latest.capo == payload.capo
        ):
            return TabCreateResponse(
                tab_id=latest.tab_id,
                segment_id=latest.segment_id,
                version=latest.version,
                status="queued",
                is_reused=True,
                created_at=latest.created_at,
            )

    next_version = (history[-1].version + 1) if history else 1
    _TAB_ID_SEQ += 1
    job = _InMemoryTabJob(
        tab_id=_TAB_ID_SEQ,
        segment_id=segment_id,
        version=next_version,
        status="queued",
        priority=payload.priority,
        tuning_id=payload.tuning_id,
        capo=payload.capo,
        created_at=datetime.now(UTC),
    )
    history.append(job)

    return TabCreateResponse(
        tab_id=job.tab_id,
        segment_id=job.segment_id,
        version=job.version,
        status="queued",
        is_reused=False,
        created_at=job.created_at,
    )
