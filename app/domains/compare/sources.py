"""다른 도메인(REC·TAB)에서 읽어 오는 값.

구간·음원·세션은 JOB과 같은 인터페이스(processing.sources)를 쓴다.
기준 타브는 tabs에서 읽는다.
연주 정보는 REC의 performances 테이블에서 조회한다.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.performance.models import Performance
from app.domains.tab.models import DONE_STATUSES as TAB_DONE_STATUSES
from app.domains.tab.models import Tab


@dataclass(frozen=True)
class PerformanceSource:
    """비교에 필요한 연주 정보 (REC performances)."""

    performance_id: int
    workspace_id: int
    track_id: int
    segment_id: int
    start_ms: int
    end_ms: int
    storage_key: str
    file_hash: str


def load_performance(
    db: Session,
    workspace_id: int,
    performance_id: int,
) -> PerformanceSource:
    """워크스페이스 소유의 연주를 조회한다."""

    performance = db.scalar(
        select(Performance).where(
            Performance.id == performance_id,
            Performance.workspace_id == workspace_id,
        )
    )

    if performance is None:
        raise BusinessException(ErrorCode.PERFORMANCE_NOT_FOUND)

    return PerformanceSource(
        performance_id=performance.id,
        workspace_id=performance.workspace_id,
        track_id=performance.track_id,
        segment_id=performance.segment_id,
        start_ms=performance.start_ms,
        end_ms=performance.end_ms,
        storage_key=performance.storage_key,
        file_hash=performance.file_hash,
    )


def find_latest_tab_id(db: Session, stem_id: int) -> int | None:
    """그 기타 음원으로 만든 최신 완료 타브 (success·needs_review, 삭제 제외)."""

    return db.scalar(
        select(Tab.id)
        .where(
            Tab.stem_id == stem_id,
            Tab.status.in_(TAB_DONE_STATUSES),
            Tab.deleted_at.is_(None),
        )
        .order_by(Tab.version.desc())
        .limit(1)
    )
