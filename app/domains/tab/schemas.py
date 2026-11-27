from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.processing.schemas import FailureView, StageView


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


class RhythmOutput(BaseModel):
    """rhythm 단계 결과 (rhythm.json)."""

    notes: list[QuantizedNote]
    tempo_bpm: float
    beats_per_bar: int | None
    non_4_4: bool
    tempo_uncertain: bool


class TabOutput(BaseModel):
    """tab 단계 결과 (tab.json). 결과 조회 API가 그대로 읽는다."""

    notes: list[TabNote]
    tempo_bpm: float
    time_signature: str = "4/4"
    tempo_uncertain: bool


# ---- API 요청·응답 ----


class TabCreateRequest(BaseModel):
    """POST /segments/{segment_id}/tabs 요청. 생략하면 기본 조건."""

    result_type: str = "original"
    tuning_id: str = "e_standard"
    capo: int = 0


class TabCreatedResponse(BaseModel):
    """POST /segments/{segment_id}/tabs 응답. 202(새 작업) / 200(재사용)."""

    tab_id: int
    segment_id: int
    stem_id: int
    version: int
    status: str
    is_reused: bool
    created_at: datetime


class TabListItem(BaseModel):
    tab_id: int
    stem_id: int
    version: int
    is_latest: bool
    result_type: str
    status: str
    tuning_id: str
    capo: int
    created_at: datetime


class TabListResponse(BaseModel):
    """GET /segments/{segment_id}/tabs 응답. 최신 version부터."""

    items: list[TabListItem]


class TabResponse(BaseModel):
    """GET /tabs/{tab_id} 응답. 작업 실패도 HTTP 200이고 failure로 알린다."""

    tab_id: int
    segment_id: int
    stem_id: int
    version: int
    status: str
    current_stage: str | None
    review_reasons: list[str]  # needs_review 이유. 처리 중에도 미리 알 수 있는 이유가 있다
    stages: list[StageView]
    failure: FailureView | None
    updated_at: datetime


class SegmentView(BaseModel):
    """타브를 만든 당시 구간 범위. notes[].start는 이 start_ms 기준이다."""

    segment_id: int
    name: str | None
    start_ms: int
    end_ms: int
    is_outdated: bool = False  # 만든 뒤 구간 범위가 바뀌었으면 True (이전 구간 기준 결과)


class ConditionView(BaseModel):
    tuning_id: str
    tuning_notes: list[str]
    strings: int
    capo: int


class ResultSummary(BaseModel):
    note_count: int
    low_confidence_count: int
    unplayable_count: int


class ResultNote(BaseModel):
    start: float  # 구간 시작 기준 초
    end: float
    pitch: int
    string: int
    fret: int
    beat: float
    duration: float
    confidence: float
    low_confidence: bool
    bend: list[float]
    playable: bool


class ModelView(BaseModel):
    config_version: str
    transcription: str
    beat: str


class TabResultResponse(BaseModel):
    """GET /tabs/{tab_id}/result 응답. 결과는 항상 확인이 필요한 초안이다."""

    tab_id: int
    stem_id: int
    version: int
    result_type: str
    status: str
    is_draft: bool = True
    segment: SegmentView
    condition: ConditionView
    tempo_bpm: float
    time_signature: str
    tempo_uncertain: bool
    review_reasons: list[str]
    summary: ResultSummary
    notes: list[ResultNote]
    musicxml_url: str
    musicxml_url_expires_at: datetime
    model: ModelView


class TabRetryResponse(BaseModel):
    """POST /tabs/{tab_id}/retry 응답."""

    tab_id: int
    status: str
    retry_from: str
    retry_count: int
    max_retries: int
    retries_left: int


class TabDownloadResponse(BaseModel):
    """GET /tabs/{tab_id}/download 응답."""

    format: str
    file_name: str
    url: str
    expires_at: datetime


class TuningView(BaseModel):
    id: str
    name: str
    strings: int
    notes: list[str]


class CapoRange(BaseModel):
    min: int
    max: int


class ResultTypeView(BaseModel):
    id: str
    name: str


class ConditionDefaults(BaseModel):
    result_type: str
    tuning_id: str
    capo: int


class ConditionsResponse(BaseModel):
    """GET /tabs/conditions 응답."""

    tunings: list[TuningView]
    capo: CapoRange
    result_types: list[ResultTypeView]
    default: ConditionDefaults
