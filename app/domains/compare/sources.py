"""다른 도메인(REC·TAB)에서 읽어 오는 값.

구간·음원·세션은 JOB과 같은 인터페이스(processing.sources)를 쓴다.
기준 타브는 tabs에서 읽는다. performances 모델이 머지되면 load_performance 본문만 바꾼다.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.tab.models import DONE_STATUSES as TAB_DONE_STATUSES
from app.domains.tab.models import Tab


@dataclass(frozen=True)
class PerformanceSource:
    """비교에 필요한 연주 정보 (REC performances)."""

    performance_id: int
    workspace_id: int
    track_id: int
    segment_id: int
    start_ms: int  # 녹음 당시 구간 범위
    end_ms: int
    storage_key: str  # 연주 파일 경로
    file_hash: str  # input_hash 계산용. REC에 없으면 storage_key로 대신한다


def load_performance(db: Session, workspace_id: int, performance_id: int) -> PerformanceSource:
    """연주를 조회한다. 없거나 다른 워크스페이스 소유면 PERFORMANCE_NOT_FOUND.

    TODO(REC): performances 모델이 머지되면 조회로 교체 (이슈 #9).
    """
    raise BusinessException(
        ErrorCode.PERFORMANCE_NOT_FOUND, "연주 조회가 아직 연결되지 않았습니다."
    )


def find_latest_tab_id(db: Session, stem_id: int) -> int | None:
    """그 기타 음원으로 만든 최신 완료 타브 (success·needs_review, 삭제 제외). 없으면 None.

    없으면 타브 없이 비교한다 (기준 타브는 결과 화면 표시용).
    """
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
