"""다른 도메인(SEG·IN·ACC)에서 읽어 오는 값.

구간은 SEG의 segments 테이블에서 읽는다. 트랙(IN)·세션(ACC)은 아직 없어서
load_track·get_current_workspace_id만 임시로 두고, 모델이 들어오면 그 함수 본문만 바꾼다.
"""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.segment.models import Segment


@dataclass(frozen=True)
class TrackSource:
    """기타 음원 준비에 필요한 음원 정보 (IN tracks)."""

    track_id: int
    workspace_id: int  # 소유권 확인 기준 (segments에는 workspace_id가 없다)
    is_separated_stem: bool
    storage_key: str  # 원본 파일 경로
    file_hash: str  # input_hash 계산용


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


def load_track(db: Session, track_id: int) -> TrackSource:
    """트랙을 조회한다. 없으면 SEGMENT_NOT_FOUND (구간의 음원이 없는 것과 같다).

    TODO(IN): tracks 모델이 머지되면 조회로 교체 (이슈 #19).
    """
    raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND, "트랙 조회가 아직 연결되지 않았습니다.")


def load_segment(db: Session, workspace_id: int, segment_id: int) -> SegmentSource:
    """구간과 그 음원을 조회한다. 없거나 다른 워크스페이스 소유면 SEGMENT_NOT_FOUND."""
    segment = db.get(Segment, segment_id)
    if segment is None:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)
    track = load_track(db, segment.track_id)
    if track.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)  # 다른 워크스페이스는 없는 것처럼
    return SegmentSource(
        workspace_id=track.workspace_id,
        track_id=segment.track_id,
        segment_id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        is_separated_stem=track.is_separated_stem,
        track_storage_key=track.storage_key,
        track_file_hash=track.file_hash,
        name=segment.name,
    )


def load_track_storage_key(db: Session, track_id: int) -> str:
    """원본 파일 경로. worker의 preprocess와 CMP 재생 정보가 쓴다."""
    return load_track(db, track_id).storage_key


def get_current_workspace_id(request: Request) -> int:
    """세션 쿠키로 확인한 워크스페이스 ID.

    TODO(ACC): 세션 의존성이 머지되면 그 함수로 교체 (이슈 #19). 그 전에는 항상 401.
    """
    raise BusinessException(ErrorCode.UNAUTHORIZED)
