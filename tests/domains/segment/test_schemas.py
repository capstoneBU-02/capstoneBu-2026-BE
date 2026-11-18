import pytest
from pydantic import ValidationError

from app.domains.segment.schemas import SegmentUpdateRequest


@pytest.mark.parametrize(
    "name",
    [
        "INTRO",
        "VERSE",
        "PRE_CHORUS",
        "CHORUS",
        "BRIDGE",
        "INTERLUDE",
        "OUTRO",
    ],
)
def test_valid_segment_names(name: str) -> None:
    request = SegmentUpdateRequest(name=name)

    assert request.name == name


@pytest.mark.parametrize(
    "name",
    [
        "HELLO",
        "verse",
        "PRE-CHORUS",
        "",
        "INTRODUCTION",
    ],
)
def test_invalid_segment_names(name: str) -> None:
    with pytest.raises(ValidationError):
        SegmentUpdateRequest(name=name)


def test_segment_name_cannot_be_null() -> None:
    with pytest.raises(ValidationError):
        SegmentUpdateRequest(name=None)


def test_empty_update_request_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SegmentUpdateRequest()


def test_start_and_end_must_be_provided_together() -> None:
    with pytest.raises(ValidationError):
        SegmentUpdateRequest(start_ms=1000)


def test_name_only_update_is_allowed() -> None:
    request = SegmentUpdateRequest(name="VERSE")

    assert request.start_ms is None
    assert request.end_ms is None
    assert request.name == "VERSE"
