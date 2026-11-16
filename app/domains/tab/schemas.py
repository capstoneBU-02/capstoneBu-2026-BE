from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlayingCondition(BaseModel):
    """연주 조건. PoC는 E 스탠다드 6현, 카포 0만 지원한다."""

    model_config = ConfigDict(frozen=True)

    tuning: tuple[str, ...] = ("E2", "A2", "D3", "G3", "B3", "E4")
    strings: int = Field(default=6, ge=1)
    capo: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_tuning_length(self) -> Self:
        if len(self.tuning) != self.strings:
            raise ValueError("tuning 개수와 strings가 다릅니다.")
        return self


class Note(BaseModel):
    """음 인식 결과. 시간은 구간 시작 기준 상대 시간(초)이다."""

    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    pitch: int = Field(ge=0, le=127)  # MIDI 번호
    velocity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    bend: tuple[float, ...] = ()  # 반음 단위 벤딩 곡선
    low_confidence: bool = False


class QuantizedNote(Note):
    """리듬 정량화까지 끝난 노트."""

    beat: float = Field(ge=0)  # 정량화된 박 위치
    duration: float = Field(ge=0)  # 박 단위 길이


class TabNote(QuantizedNote):
    """줄·프렛이 정해진 노트."""

    string: int = Field(ge=1)  # 1(고음 E)부터
    fret: int = Field(ge=0)  # 0은 개방현
    playable: bool = True
