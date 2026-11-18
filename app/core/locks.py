"""DB 잠금(pg_advisory_xact_lock) 첫 번째 키. 도메인마다 달라야 하므로 여기 한곳에서만 정한다.

두 번째 키는 각 도메인이 정한다 (stems·tabs=구간 ID, comparisons=연주 ID).
첫 번째 키가 겹치면 서로 관계없는 요청끼리 기다리게 된다 (#54).
"""

from enum import IntEnum, unique


@unique
class LockClass(IntEnum):
    STEMS = 1
    TABS = 2
    COMPARISONS = 3
