"""HTTP contract tests for POST /api/download/prepare."""

import uuid

from fastapi.testclient import TestClient

from app.domain import MediaManifest, MediaResource, MediaType
from app.infrastructure.database import session_scope
from app.infrastructure.models import DownloadTask, ParseTask
from app.main import create_app


def test_prepare_endpoint_accepts_guest_and_forbids_extra_fields(env):
    settings, engine = env[:2]
    task_id = str(uuid.uuid4())
    manifest = MediaManifest(
        kind="video",
        videos=(MediaResource(url="https://cdn.example/video.mp4", format="mp4"),),
    )
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url="https://source.example/item",
                platform="test",
                media_type=MediaType.VIDEO,
                title="Guest title",
                metadata_={"manifest": manifest.model_dump(mode="json")},
            )
        )

    response = TestClient(create_app(settings=settings)).post(
        "/api/download/prepare",
        json={"task_id": task_id, "asset": {"kind": "video"}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "direct"

    extra = TestClient(create_app(settings=settings)).post(
        "/api/download/prepare",
        json={
            "task_id": task_id,
            "asset": {"kind": "video"},
            "unexpected": True,
        },
    )
    assert extra.status_code == 400
    assert extra.json()["code"] == 400


def test_prepare_endpoint_stages_streaming_asset_once(env):
    settings, engine = env[:2]
    task_id = str(uuid.uuid4())
    manifest = MediaManifest(
        kind="video",
        videos=(
            MediaResource(
                url="https://cdn.example/MANIFEST.M3U8?token=private",
                format="mp4",
            ),
        ),
    )
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url="https://source.example/item",
                platform="test",
                media_type=MediaType.VIDEO,
                title="Stream",
                metadata_={"manifest": manifest.model_dump(mode="json")},
            )
        )

    response = TestClient(create_app(settings=settings)).post(
        "/api/download/prepare",
        json={"task_id": task_id, "asset": {"kind": "video"}},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "staged"
    with session_scope(engine) as session:
        rows = session.query(DownloadTask).filter_by(task_id=task_id).all()
    assert len(rows) == 1
