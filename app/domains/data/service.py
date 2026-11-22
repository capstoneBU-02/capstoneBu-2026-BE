import importlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.config import get_settings
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare.models import Comparison, ComparisonStageResult
from app.domains.data.models import FileAccessLog
from app.domains.data.schemas import (
    PaginationMeta,
    PresignedUrlRequest,
    PresignedUrlResponse,
    StorageUsageMeta,
    TaskDetailResponse,
    WorkspaceTaskItem,
    WorkspaceTaskListResponse,
)
from app.domains.input.models import Track
from app.domains.processing.models import Stem, StemStageResult
from app.domains.segment.models import Segment
from app.domains.tab import keys as tab_keys
from app.domains.tab.models import DONE_STATUSES as TAB_DONE_STATUSES
from app.domains.tab.models import Tab, TabStageResult

try:
    Performance = importlib.import_module("app.domains.performance.models").Performance
except ImportError:  # REC(#70) 머지 전 브랜치 호환
    Performance = None


def _workspace_prefix(workspace_id: int) -> str:
    """저장소 key 규칙: {workspace_id}/..."""
    return f"{workspace_id}/"


def _collect_deleted_keys(db: Session, workspace_id: int) -> set[str]:
    """소프트 삭제된 자원(Stem, Tab, Comparison)에 연결된 모든 파일 키를 수집한다."""
    deleted_keys: set[str] = set()

    deleted_stems = db.scalars(
        select(Stem).where(Stem.workspace_id == workspace_id, Stem.deleted_at.is_not(None))
    ).all()
    deleted_stem_ids: list[int] = []
    for stem in deleted_stems:
        deleted_stem_ids.append(stem.id)
        if stem.storage_key:
            deleted_keys.add(stem.storage_key)
        if stem.part_keys:
            deleted_keys.update(stem.part_keys.values())
    if deleted_stem_ids:
        stage_keys = db.scalars(
            select(StemStageResult.output_key).where(
                StemStageResult.stem_id.in_(deleted_stem_ids),
                StemStageResult.output_key.is_not(None),
            )
        ).all()
        deleted_keys.update(k for k in stage_keys if k)

    deleted_tabs = db.scalars(
        select(Tab).where(Tab.workspace_id == workspace_id, Tab.deleted_at.is_not(None))
    ).all()
    deleted_tab_ids: list[int] = []
    for tab in deleted_tabs:
        deleted_tab_ids.append(tab.id)
        deleted_keys.add(tab_keys.musicxml_key(tab))
        deleted_keys.add(tab_keys.tab_json_key(tab))
    if deleted_tab_ids:
        tab_stage_keys = db.scalars(
            select(TabStageResult.output_key).where(
                TabStageResult.tab_id.in_(deleted_tab_ids),
                TabStageResult.output_key.is_not(None),
            )
        ).all()
        deleted_keys.update(k for k in tab_stage_keys if k)

    deleted_comparisons = db.scalars(
        select(Comparison).where(
            Comparison.workspace_id == workspace_id, Comparison.deleted_at.is_not(None)
        )
    ).all()
    deleted_comp_ids: list[int] = []
    for comp in deleted_comparisons:
        deleted_comp_ids.append(comp.id)
        if comp.result_key:
            deleted_keys.add(comp.result_key)
    if deleted_comp_ids:
        comp_stage_keys = db.scalars(
            select(ComparisonStageResult.output_key).where(
                ComparisonStageResult.comparison_id.in_(deleted_comp_ids),
                ComparisonStageResult.output_key.is_not(None),
            )
        ).all()
        deleted_keys.update(k for k in comp_stage_keys if k)

    return deleted_keys


def _calculate_workspace_storage_bytes(db: Session, workspace_id: int) -> int:
    """워크스페이스 전체 스토리지 사용량(원곡 + 분리 결과 + 연주 + 타브)을 계산한다."""
    # 1. 원곡 트랙 크기 (DB 컬럼 합산)
    track_bytes = int(
        db.scalar(
            select(func.coalesce(func.sum(Track.file_size_bytes), 0)).where(
                Track.workspace_id == workspace_id
            )
        )
        or 0
    )

    # 2. 분리 결과(Stem), 연주(Performance), 타브(Tab) 객체 키 수집 후 스토리지 크기 합산
    object_keys: set[str] = set()

    active_stems = db.scalars(
        select(Stem).where(Stem.workspace_id == workspace_id, Stem.deleted_at.is_(None))
    ).all()
    for stem in active_stems:
        if stem.storage_key:
            object_keys.add(stem.storage_key)
        if stem.part_keys:
            object_keys.update(stem.part_keys.values())

    performances = db.scalars(
        select(Performance).where(Performance.workspace_id == workspace_id)
    ).all()
    for perf in performances:
        if perf.storage_key:
            object_keys.add(perf.storage_key)

    active_tabs = db.scalars(
        select(Tab).where(
            Tab.workspace_id == workspace_id,
            Tab.deleted_at.is_(None),
            Tab.status.in_(TAB_DONE_STATUSES),
        )
    ).all()
    for tab in active_tabs:
        object_keys.add(tab_keys.musicxml_key(tab))
        object_keys.add(tab_keys.tab_json_key(tab))

    derived_bytes = sum(storage.object_size(key) for key in object_keys)
    return track_bytes + derived_bytes


def list_workspace_tasks(
    db: Session,
    workspace_id: int,
    page: int = 1,
    limit: int = 20,
) -> WorkspaceTaskListResponse:
    """DATA-02: 현재 워크스페이스의 이전 작업 이력(타브·분리·비교) 및 스토리지 사용량을 조회한다."""
    settings = get_settings()

    # 1. 타브 생성 작업
    tab_rows = db.execute(
        select(Tab, Segment, Track, Stem)
        .join(Segment, Tab.segment_id == Segment.id)
        .join(Track, Tab.track_id == Track.id)
        .outerjoin(Stem, Tab.stem_id == Stem.id)
        .where(
            Tab.workspace_id == workspace_id,
            Tab.deleted_at.is_(None),
        )
    ).all()

    # 2. 음원 분리 작업 (타브가 연결되지 않은 단독 분리 작업 포함)
    stem_rows = db.execute(
        select(Stem, Segment, Track)
        .join(Segment, Stem.segment_id == Segment.id)
        .join(Track, Stem.track_id == Track.id)
        .where(
            Stem.workspace_id == workspace_id,
            Stem.deleted_at.is_(None),
        )
    ).all()

    # 3. 연주 비교 작업
    comparison_rows = db.execute(
        select(Comparison, Segment, Track, Stem)
        .join(Segment, Comparison.segment_id == Segment.id)
        .join(Track, Comparison.track_id == Track.id)
        .outerjoin(Stem, Comparison.stem_id == Stem.id)
        .where(
            Comparison.workspace_id == workspace_id,
            Comparison.deleted_at.is_(None),
        )
    ).all()

    all_items: list[WorkspaceTaskItem] = []

    for tab, segment, track, stem in tab_rows:
        start_ms = stem.start_ms if stem is not None else segment.start_ms
        end_ms = stem.end_ms if stem is not None else segment.end_ms
        target_part = stem.part if stem is not None else "guitar"
        all_items.append(
            WorkspaceTaskItem(
                task_id=tab.id,
                task_type="tab",
                segment_id=tab.segment_id,
                track_id=tab.track_id,
                parent_task_id=None,
                version=tab.version,
                track_filename=track.filename,
                start_sec=round(start_ms / 1000.0, 3),
                end_sec=round(end_ms / 1000.0, 3),
                target_part=target_part,
                priority=tab.result_type,
                status=tab.status,
                is_reused=False,
                created_at=tab.created_at,
            )
        )

    for stem, _segment, track in stem_rows:
        all_items.append(
            WorkspaceTaskItem(
                task_id=stem.id,
                task_type="stem",
                segment_id=stem.segment_id,
                track_id=stem.track_id,
                parent_task_id=None,
                version=stem.version,
                track_filename=track.filename,
                start_sec=round(stem.start_ms / 1000.0, 3),
                end_sec=round(stem.end_ms / 1000.0, 3),
                target_part=stem.part,
                priority="separation",
                status=stem.status,
                is_reused=False,
                created_at=stem.created_at,
            )
        )

    for comp, _segment, track, stem in comparison_rows:
        target_part = stem.part if stem is not None else "guitar"
        all_items.append(
            WorkspaceTaskItem(
                task_id=comp.id,
                task_type="comparison",
                segment_id=comp.segment_id,
                track_id=comp.track_id,
                parent_task_id=None,
                version=1,
                track_filename=track.filename,
                start_sec=round(comp.start_ms / 1000.0, 3),
                end_sec=round(comp.end_ms / 1000.0, 3),
                target_part=target_part,
                priority="comparison",
                status=comp.status,
                is_reused=False,
                created_at=comp.created_at,
            )
        )

    # 최신순 정렬 후 페이지네이션
    all_items.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
    total_count = len(all_items)
    offset = (page - 1) * limit
    paged_items = all_items[offset : offset + limit]

    usage_bytes = _calculate_workspace_storage_bytes(db, workspace_id)

    return WorkspaceTaskListResponse(
        items=paged_items,
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
    """DATA-02: 특정 이전 작업의 세부 결과 및 관련 자원 ID를 재열람한다 (tabs·stems 기준)."""
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
        status=tab.status,
        is_reused=False,
        created_at=tab.created_at,
    )


def create_presigned_file_url(
    db: Session,
    workspace_id: int,
    req: PresignedUrlRequest,
) -> PresignedUrlResponse:
    """DATA-05: 비공개 파일의 워크스페이스 소유권 검증 후 임시 Presigned URL 발급 및 로그 기록."""
    normalized_key = req.file_key.lstrip("/")
    required_prefix = _workspace_prefix(workspace_id)

    # 경로 탐색(../) 차단 및 워크스페이스 소유권 접두사({workspace_id}/) 검증
    if ".." in normalized_key.split("/") or not normalized_key.startswith(required_prefix):
        raise BusinessException(ErrorCode.FILE_ACCESS_DENIED)

    # 소프트 삭제된 자원에 연결된 파일은 발급 차단
    if normalized_key in _collect_deleted_keys(db, workspace_id):
        raise BusinessException(ErrorCode.FILE_NOT_FOUND)

    if not storage.file_exists(normalized_key):
        raise BusinessException(ErrorCode.FILE_NOT_FOUND)

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=req.expires_in_seconds)

    download_name = normalized_key.rsplit("/", 1)[-1] if req.access_type == "DOWNLOAD" else None
    presigned_url = storage.presigned_download_url(
        normalized_key,
        download_name=download_name,
        expires_in_seconds=req.expires_in_seconds,
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