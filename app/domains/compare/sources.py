"""다른 도메인(REC·TAB)에서 읽어 오는 값.

구간·음원·세션은 JOB과 같은 인터페이스(processing.sources)를 쓴다.
performances·tabs 모델이 머지되면 아래 함수 본문만 실제 조회로 바꾼다.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode


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
    """그 기타 음원으로 만든 최신 타브 (success·needs_review). 없으면 None.

    TODO(TAB): tabs 모델·stem_id 컬럼이 머지되면 조회로 교체 (이슈 #9).
    그 전에는 타브 없이 비교한다.
    """
    return None
