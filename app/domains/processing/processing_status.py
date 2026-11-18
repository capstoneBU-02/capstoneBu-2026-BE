"""구간 처리 상태 통합 조회 (GET /segments/{segment_id}/processing-status, JOB-03·JOB-04).

기타 음원(JOB)과 타브(TAB) 상태를 하나로 합치지 않고 따로 돌려준다.
그래서 "분리 성공, 타브 실패"를 그대로 표시할 수 있다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.domains.processing import service, sources
from app.domains.processing.models import Stem
from app.domains.processing.schemas import (
    ProcessingStatusResponse,
    StemStatusView,
    TabStatusView,
)
from app.domains.tab import service as tab_service
from app.domains.tab.models import Tab


def _latest_tab(db: Session, stem_id: int) -> Tab | None:
    """그 기타 음원으로 만든 최신 타브 (상태와 상관없이, 삭제 제외)."""
    return db.scalars(
        select(Tab)
        .where(Tab.stem_id == stem_id, Tab.deleted_at.is_(None))
        .order_by(Tab.version.desc())
        .limit(1)
    ).first()


def _stem_view(stem: Stem) -> StemStatusView:
    return StemStatusView(
        stem_id=stem.id,
        status=stem.status,
        current_stage=stem.current_stage,
        playable=service.is_playable(stem),
        retryable=service.is_retryable(stem),
    )


def _tab_view(tab: Tab) -> TabStatusView:
    return TabStatusView(
        tab_id=tab.id,
        status=tab.status,
        current_stage=tab.current_stage,
        retryable=tab_service.is_retryable(tab),  # GET /tabs/{id}와 같은 판단
    )


def get_processing_status(
    db: Session, workspace_id: int, segment_id: int
) -> ProcessingStatusResponse:
    """구간의 현재 기타 음원과 그 음원으로 만든 최신 타브. 요청하지 않은 작업은 null.

    구간이 없거나 다른 워크스페이스 소유면 SEGMENT_NOT_FOUND.
    """
    seg = sources.load_segment(db, workspace_id, segment_id)
    stem = service.get_current_stem(db, seg.segment_id, seg.start_ms, seg.end_ms)
    if stem is not None and service.is_stale(stem):
        # 멈춘 작업일 때만 잠가서 실패로 정리한다 (GET /stems/{id}와 같은 판단).
        # 그 사이 삭제됐으면 요청하지 않은 것처럼 null
        try:
            stem = service.get_owned_stem(db, workspace_id, stem.id)
        except BusinessException:
            stem = None
    # 아래에서 타브를 정리하며 commit하면 stem 객체가 만료되므로 응답 값을 먼저 만든다
    stem_view = _stem_view(stem) if stem is not None else None
    stem_updated = stem.updated_at if stem is not None else None

    tab = _latest_tab(db, stem.id) if stem is not None else None
    if stem is not None and tab is not None and tab_service.is_stale(tab):
        # 멈춘 타브도 같은 방식으로 정리한다 (GET /tabs/{id}와 같은 판단).
        # 그 사이 삭제됐으면 남아 있는 최신 타브를 보여 준다
        try:
            tab = tab_service.get_owned_tab(db, workspace_id, tab.id)
        except BusinessException:
            tab = _latest_tab(db, stem.id)

    times = [t for t in (stem_updated, tab.updated_at if tab is not None else None) if t]
    return ProcessingStatusResponse(
        segment_id=seg.segment_id,
        stem=stem_view,
        tab=_tab_view(tab) if tab is not None else None,
        updated_at=max(times) if times else None,
    )
