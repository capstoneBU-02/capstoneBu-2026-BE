"""#61 기타를 뺀 반주와 악기별 음원 저장·재생."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.processing import keys, service, sources, tasks
from app.domains.processing.models import Stem, StemStage, StemStatus
from app.domains.processing.pipeline import separation
from app.domains.processing.tasks import StageOutcome, run_stage
from app.main import create_app
from tests.domains.conftest import EnqueueSpy, FakeSegments

WS = 10
PARTS = ("bass", "drums", "other", "piano", "vocals")


@pytest.fixture
def stem(db: Session, segments: FakeSegments, enqueue: EnqueueSpy) -> Stem:
    segments.add(segment_id=1)
    return service.request_stem(db, WS, 1, enqueue=enqueue).stem


@pytest.fixture
def presign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.storage, "presigned_download_url", lambda key: f"https://s3/{key}")


@pytest.fixture
def api(session_factory: Callable[[], Session]) -> Iterator[TestClient]:
    app = create_app()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[sources.get_current_workspace_id] = lambda: WS
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _ready(db: Session, stem: Stem, part_keys: dict[str, str] | None) -> None:
    stem.status = StemStatus.SUCCESS
    stem.storage_key = keys.guitar_key(stem)
    stem.part_keys = part_keys
    db.commit()


# ---- DB 없이 ----


def test_part_name_reads_both_model_formats() -> None:
    assert separation.part_name(Path("input_(bass)_BS-Roformer-SW.wav")) == "bass"
    assert separation.part_name(Path("/tmp/input_(Guitar)_htdemucs_6s.wav")) == "guitar"
    assert separation.part_name(Path("input.wav")) is None


def test_part_key_follows_guitar_key() -> None:
    s = Stem(id=7, workspace_id=10, track_id=3, segment_id=5)
    assert keys.guitar_key(s) == "10/3/stems/5/7/guitar.wav"
    assert keys.part_key(s, "backing") == "10/3/stems/5/7/backing.wav"
    for bad in ("../x", "Bass", "", "a/b"):
        with pytest.raises(ValueError):
            keys.part_key(s, bad)


def test_mix_sums_and_clips(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    a, b, out = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "out.wav"
    sf.write(a, np.full((100, 2), 0.25, dtype="float32"), 8000, subtype="FLOAT")
    sf.write(b, np.full((90, 2), 0.9, dtype="float32"), 8000, subtype="FLOAT")
    separation.mix([str(a), str(b)], str(out))
    data, sr = sf.read(out, dtype="float32", always_2d=True)
    assert sr == 8000 and data.shape == (90, 2)  # 짧은 쪽 길이
    assert data.max() == pytest.approx(1.0, abs=1e-3)  # 1.15 → 클리핑


# ---- 분리 작업 ----


def test_separate_work_uploads_parts_and_backing(
    db: Session, stem: Stem, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """분리하면 기타와 함께 악기별 음원·반주를 올리고 part_keys로 돌려준다."""
    uploads: list[str] = []
    monkeypatch.setattr(tasks.storage, "download_file", lambda key, path: None)
    monkeypatch.setattr(tasks.storage, "upload_file", lambda path, key, ct: uploads.append(key))
    monkeypatch.setattr(separation, "active_ratio", lambda path, silence_dbfs: 0.9)

    def fake_separate(src: str, out: str, **kw: object) -> separation.SeparationResult:
        parts_dir = Path(str(kw["parts_dir"]))
        assert parts_dir.is_dir()
        parts = {p: str(parts_dir / f"{p}.wav") for p in (*PARTS, "backing")}
        return separation.SeparationResult(
            model="m", input_dbfs=-10.0, guitar_dbfs=-12.0, parts=parts
        )

    monkeypatch.setattr(separation, "separate_guitar", fake_separate)

    out = tasks.separate_work(db, stem, tmp_path)

    expected = {p: keys.part_key(stem, p) for p in (*PARTS, "backing")}
    assert out.part_keys == expected
    assert out.output_key == keys.guitar_key(stem)
    assert sorted(uploads) == sorted([keys.guitar_key(stem), *expected.values()])


def test_bypass_has_no_parts(
    db: Session, stem: Stem, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stem.bypass_separation = True
    stem.input_pad_ms = 0
    monkeypatch.setattr(tasks.storage, "download_file", lambda key, path: None)
    monkeypatch.setattr(tasks.storage, "upload_file", lambda path, key, ct: None)
    monkeypatch.setattr(separation, "trim", lambda *a: None)
    monkeypatch.setattr(separation, "active_ratio", lambda path, silence_dbfs: 0.9)
    assert tasks.separate_work(db, stem, tmp_path).part_keys is None


def test_last_stage_saves_part_keys(
    db: Session, stem: Stem, session_factory: Callable[[], Session]
) -> None:
    pk = {"backing": "b.wav", "bass": "bass.wav"}

    def work(db: Session, s: Stem, tmp: Path) -> StageOutcome:
        return StageOutcome(output_key="g.wav", model="m", part_keys=pk)

    assert run_stage(
        stem.id, StemStage.PREPROCESS, lambda *a: StageOutcome("in.wav"), session_factory
    )
    assert run_stage(stem.id, StemStage.SEPARATION, work, session_factory)
    db.expire_all()
    s = db.get(Stem, stem.id)
    assert s is not None and s.status == StemStatus.SUCCESS
    assert (s.storage_key, s.part_keys) == ("g.wav", pk)


# ---- 응답·재생 ----


def test_stem_response_backing_playable(db: Session, stem: Stem) -> None:
    assert service.to_stem_response(db, stem).backing_playable is False  # 준비 전
    _ready(db, stem, {"backing": keys.part_key(stem, "backing")})
    assert service.to_stem_response(db, stem).backing_playable is True


def test_playback_returns_guitar_and_backing(db: Session, stem: Stem, presign: None) -> None:
    _ready(db, stem, {"backing": keys.part_key(stem, "backing")})
    res = service.get_playback(db, WS, stem.id)
    assert res.is_partial is False
    assert [(t.type, t.available, t.url) for t in res.tracks] == [
        ("guitar", True, f"https://s3/{keys.guitar_key(stem)}"),
        ("backing", True, f"https://s3/{keys.part_key(stem, 'backing')}"),
    ]


def test_playback_without_backing_is_partial(db: Session, stem: Stem, presign: None) -> None:
    """분리를 건너뛴 음원이나 #61 이전 결과는 반주가 없다."""
    _ready(db, stem, None)
    res = service.get_playback(db, WS, stem.id)
    assert res.is_partial is True
    assert [(t.type, t.available, t.url) for t in res.tracks][1] == ("backing", False, None)


def test_playback_not_ready_409(db: Session, stem: Stem) -> None:
    with pytest.raises(BusinessException) as e:
        service.get_playback(db, WS, stem.id)
    assert e.value.error_code == ErrorCode.STEM_NOT_READY


def test_playback_other_workspace_404(db: Session, stem: Stem) -> None:
    _ready(db, stem, None)
    with pytest.raises(BusinessException) as e:
        service.get_playback(db, 99, stem.id)
    assert e.value.error_code == ErrorCode.STEM_NOT_FOUND


def test_playback_route(api: TestClient, db: Session, stem: Stem, presign: None) -> None:
    assert api.get(f"/api/v1/stems/{stem.id}/playback").status_code == 409
    _ready(db, stem, {"backing": keys.part_key(stem, "backing")})
    body = api.get(f"/api/v1/stems/{stem.id}/playback").json()["data"]
    assert body["is_partial"] is False and len(body["tracks"]) == 2
