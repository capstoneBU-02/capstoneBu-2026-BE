from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.data.models import FileAccessLog, MediaTask, TabResult
from app.domains.data.schemas import (
    DataTabDownloadResponse,
    PaginationMeta,
    PresignedUrlRequest,
    PresignedUrlResponse,
    StorageUsageMeta,
    TaskDetailResponse,
    WorkspaceTaskItem,
    WorkspaceTaskListResponse,
)
from app.domains.input.models import Track
from app.domains.processing.models import Stem
from app.domains.segment.models import Segment
from app.domains.tab import keys as tab_keys
from app.domains.tab.models import DONE_STATUSES, Tab, TabStatus

SUPPORTED_DOWNLOAD_FORMATS = frozenset({"musicxml"})
SUPPORTED_PAGE_OPTIONS = frozenset({"all", "single"})
PRESIGNED_DOWNLOAD_EXPIRES_SEC = 600


def _map_tab_status(raw_status: str) -> str:
    """내부 TabStatus(success 등)를 DATA 명세 상태(completed 등)로 매핑한다."""
    if raw_status == TabStatus.SUCCESS:
        return "completed"
    return raw_status


def _normalize_workspace_prefixes(workspace_id: int) -> tuple[str, ...]:
    """저장소 key 규칙({workspace_id}/... 및 ws_{workspace_id}/...) 접두사."""
    return (f"{workspace_id}/", f"ws_{workspace_id}/")


def get_tab_download(
    db: Session,
    workspace_id: int,
    tab_id: int,
    file_format: str = "musicxml",
    page_option: str = "all",
) -> DataTabDownloadResponse:
    """DATA-01: 생성 완료된 타브 악보의 보안 다운로드 링크를 발급한다."""
    if file_format not in SUPPORTED_DOWNLOAD_FORMATS:
        raise BusinessException(ErrorCode.UNSUPPORTED_DOWNLOAD_FORMAT)
    if page_option not in SUPPORTED_PAGE_OPTIONS:
        raise BusinessException(ErrorCode.INVALID_INPUT)

    # 1. tab_results 테이블 우선 확인 후, 기존 tabs 테이블과 연동 조회
    tab_result = db.get(TabResult, tab_id)
    tab = db.scalars(
        select(Tab).where(Tab.id == tab_id, Tab.deleted_at.is_(None))
    ).first()

    if tab is None and tab_result is None:
        raise BusinessException(ErrorCode.TAB_NOT_FOUND)

    owner_workspace_id = tab.workspace_id if tab is not None else tab_result.workspace_id  # type: ignore[union-attr]
    if owner_workspace_id != workspace_id:
        raise BusinessException(ErrorCode.TAB_NOT_FOUND)

    if tab is not None and tab.status not in DONE_STATUSES:
        raise BusinessException(ErrorCode.TAB_NOT_READY)

    segment_id = tab.segment_id if tab is not None else tab_result.segment_id  # type: ignore[union-attr]
    track_id = tab.track_id if tab is not None else 0
    version = tab.version if tab is not None else 1

    if tab_result is not None:
        file_key = tab_result.file_key
    else:
        assert tab is not None
        file_key = tab_keys.musicxml_key(tab)

    file_name = f"track_{track_id}_segment_{segment_id}_v{version}.{file_format}"
    expires_at = datetime.now(UTC) + timedelta(seconds=PRESIGNED_DOWNLOAD_EXPIRES_SEC)
    download_url = storage.presigned_get_url(
        file_key,
        expires_in_seconds=PRESIGNED_DOWNLOAD_EXPIRES_SEC,
        download_name=file_name,
    )

    return DataTabDownloadResponse(
        tab_id=tab_id,
        segment_id=segment_id,
        format=file_format,
        page_option=page_option,
        file_name=file_name,
        download_url=download_url,
        expires_at=expires_at,
    )


def list_workspace_tasks(
    db: Session,
    workspace_id: int,
    page: int = 1,
    limit: int = 20,
) -> WorkspaceTaskListResponse:
    """DATA-02: 현재 워크스페이스에 보관된 이전 작업 내역 및 스토리지 사용량을 조회한다."""
    settings = get_settings()

    total_count = int(
        db.scalar(
            select(func.count(Tab.id)).where(
                Tab.workspace_id == workspace_id,
                Tab.deleted_at.is_(None),
            )
        )
        or 0
    )

    offset = (page - 1) * limit
    rows = db.execute(
        select(Tab, Segment, Track, Stem)
        .join(Segment, Tab.segment_id == Segment.id)
        .join(Track, Tab.track_id == Track.id)
        .outerjoin(Stem, Tab.stem_id == Stem.id)
        .where(
            Tab.workspace_id == workspace_id,
            Tab.deleted_at.is_(None),
        )
        .order_by(Tab.created_at.desc(), Tab.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    # media_tasks 테이블에 매핑된 이력이 있으면 parent_task_id, is_reused 참조
    tab_ids = [row.Tab.id for row in rows]
    media_task_map: dict[int, MediaTask] = {}
    if tab_ids:
        mt_rows = db.scalars(
            select(MediaTask).where(MediaTask.task_id.in_(tab_ids))
        ).all()
        media_task_map = {mt.task_id: mt for mt in mt_rows}

    items: list[WorkspaceTaskItem] = []
    for tab, segment, track, stem in rows:
        mt = media_task_map.get(tab.id)
        start_ms = stem.start_ms if stem is not None else segment.start_ms
        end_ms = stem.end_ms if stem is not None else segment.end_ms
        target_part = stem.part if stem is not None else "guitar"

        items.append(
            WorkspaceTaskItem(
                task_id=tab.id,
                segment_id=tab.segment_id,
                track_id=tab.track_id,
                parent_task_id=mt.parent_task_id if mt is not None else None,
                version=tab.version,
                track_filename=track.filename,
                start_sec=round(start_ms / 1000.0, 3),
                end_sec=round(end_ms / 1000.0, 3),
                target_part=target_part,
                priority=tab.result_type,
                status=_map_tab_status(tab.status),
                is_reused=mt.is_reused if mt is not None else False,
                created_at=tab.created_at,
            )
        )

    # 워크스페이스 전체 스토리지 사용량 합산 (tracks + tab_results)
    track_bytes = int(
        db.scalar(
            select(func.coalesce(func.sum(Track.file_size_bytes), 0)).where(
                Track.workspace_id == workspace_id
            )
        )
        or 0
    )
    tab_bytes = int(
        db.scalar(
            select(func.coalesce(func.sum(TabResult.file_size_bytes), 0)).where(
                TabResult.workspace_id == workspace_id
            )
        )
        or 0
    )
    usage_bytes = track_bytes + tab_bytes

    return WorkspaceTaskListResponse(
        items=items,
        pagination=PaginationMeta(
            page=page,
            limit=limit,
            total_count=total_count,
        ),
        storage=StorageUsageMeta(
            usage_bytes=usage_bytes,
            storage_limit_exceeded=usage_bytes > settings.workspace_max_storage_bytes,
        ),
    )


def get_task_detail(
    db: Session,
    workspace_id: int,
    task_id: int,
) -> TaskDetailResponse:
    """DATA-02: 특정 이전 작업의 세부 결과 및 관련 자원 ID를 재열람한다."""
    row = db.execute(
        select(Tab, Segment, Stem)
        .join(Segment, Tab.segment_id == Segment.id)
        .outerjoin(Stem, Tab.stem_id == Stem.id)
        .where(
            Tab.id == task_id,
            Tab.deleted_at.is_(None),
        )
    ).first()

    if row is None or row.Tab.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND)

    tab, segment, stem = row.Tab, row.Segment, row.Stem
    mt = db.get(MediaTask, task_id)
    start_ms = stem.start_ms if stem is not None else segment.start_ms
    end_ms = stem.end_ms if stem is not None else segment.end_ms

    return TaskDetailResponse(
        task_id=tab.id,
        segment_id=tab.segment_id,
        track_id=tab.track_id,
        stem_id=tab.stem_id,
        tab_id=tab.id,
        version=tab.version,
        start_sec=round(start_ms / 1000.0, 3),
        end_sec=round(end_ms / 1000.0, 3),
        priority=tab.result_type,
        tuning_id=tab.tuning_id,
        capo=tab.capo,
        status=_map_tab_status(tab.status),
        is_reused=mt.is_reused if mt is not None else False,
        created_at=tab.created_at,
    )


def create_presigned_file_url(
    db: Session,
    workspace_id: int,
    req: PresignedUrlRequest,
) -> PresignedUrlResponse:
    """DATA-05: 비공개 파일의 워크스페이스 소유권 검증 후 임시 Presigned URL 발급 및 로그 기록."""
    normalized_key = req.file_key.lstrip("/")
    allowed_prefixes = _normalize_workspace_prefixes(workspace_id)

    # 경로 탐색(../) 차단 및 워크스페이스 소유권 접두사 검증
    if ".." in normalized_key.split("/") or not normalized_key.startswith(allowed_prefixes):
        raise BusinessException(ErrorCode.FILE_ACCESS_DENIED)

    if not storage.file_exists(normalized_key):
        raise BusinessException(ErrorCode.FILE_NOT_FOUND)

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=req.expires_in_seconds)

    download_name = normalized_key.rsplit("/", 1)[-1] if req.access_type == "DOWNLOAD" else None
    presigned_url = storage.presigned_get_url(
        normalized_key,
        expires_in_seconds=req.expires_in_seconds,
        download_name=download_name,
    )

    access_log = FileAccessLog(
        workspace_id=workspace_id,
        file_key=normalized_key,
        access_type=req.access_type.value,
        issued_at=now,
        expires_at=expires_at,
    )
    db.add(access_log)
    db.commit()

    return PresignedUrlResponse(
        file_key=normalized_key,
        presigned_url=presigned_url,
        expires_at=expires_at,
    )