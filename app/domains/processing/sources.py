"""다른 도메인(SEG·IN·ACC)에서 읽어 오는 값.

segments·tracks·workspaces 모델이 아직 없어서, 이 도메인이 필요한 값만 여기서 정의한다.
각 모델이 들어오면 아래 함수 본문만 실제 조회로 바꾸고 나머지 코드는 그대로 둔다.
"""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode


@dataclass(frozen=True)
class SegmentSource:
    """기타 음원 준비에 필요한 구간·음원 정보."""

    workspace_id: int
    track_id: int
    segment_id: int
    start_ms: int  # 현재 구간 범위
    end_ms: int
    is_separated_stem: bool  # tracks.is_separated_stem
    track_storage_key: str  # 원본 파일 경로
    track_file_hash: str  # 원본 파일 해시 (input_hash 계산용)
    name: str | None = None  # 구성 이름 (INTRO, VERSE ...). 비교 결과 표시에 쓴다


def load_segment(db: Session, workspace_id: int, segment_id: int) -> SegmentSource:
    """구간과 그 음원을 조회한다. 없거나 다른 워크스페이스 소유면 SEGMENT_NOT_FOUND.

    TODO(SEG·IN): segments·tracks 모델이 머지되면 조회로 교체 (이슈 #7).
    """
    raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND, "구간 조회가 아직 연결되지 않았습니다.")


def load_track_storage_key(db: Session, track_id: int) -> str:
    """원본 파일 경로. worker의 preprocess가 쓴다.

    TODO(IN): tracks 모델이 머지되면 조회로 교체 (이슈 #7).
    """
    raise NotImplementedError("tracks 모델 연결 전")


def get_current_workspace_id(request: Request) -> int:
    """세션 쿠키로 확인한 워크스페이스 ID.

    TODO(ACC): 세션 의존성이 머지되면 그 함수로 교체 (이슈 #7). 그 전에는 항상 401.
    """
    raise BusinessException(ErrorCode.UNAUTHORIZED)
