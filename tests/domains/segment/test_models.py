from sqlalchemy import BigInteger

from app.domains.segment.models import Segment


def test_segment_table_name() -> None:
    assert Segment.__tablename__ == "segments"


def test_segment_required_columns() -> None:
    columns = Segment.__table__.columns

    required = {
        "id",
        "track_id",
        "start_ms",
        "end_ms",
        "name",
        "version",
        "created_at",
        "updated_at",
    }

    assert required.issubset(set(columns.keys()))


def test_segment_time_columns_are_bigint() -> None:
    columns = Segment.__table__.columns

    assert isinstance(columns["start_ms"].type, BigInteger)
    assert isinstance(columns["end_ms"].type, BigInteger)


def test_segment_name_is_nullable() -> None:
    assert Segment.__table__.columns["name"].nullable is True
