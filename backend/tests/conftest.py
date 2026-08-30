"""Shared pytest fixtures and helpers for the backend test suite.

Besides the process-wide secrets and the hermetic ``.env`` handling, this
module hosts the worker-shaped test environment (``env``/``engine``/
``storage``/``token_provider``) and the seeding helpers both worker test files
use (``seed_parse_task``/``seed_download``/``load_download``/``FakeHub``/
``make_settings``). Test modules that define a fixture or helper of the same
name locally shadow these — every other test file already does for ``env``/
``engine``/``storage``, which return module-specific shapes there.
"""

import hashlib
import os
import uuid

import pytest

from app.adapters.factory import get_one_time_token_provider, get_storage
from app.domain import DownloadStatus, MediaType
from app.infrastructure import seed
from app.infrastructure.config import Settings
from app.infrastructure.database import Base, build_engine, session_scope
from app.infrastructure.models import DownloadTask, ParseTask

# Importing ``app.main`` constructs the module-level FastAPI instance, which
# calls ``get_settings()`` and therefore requires the two secrets. Provide safe
# local values for the whole test process (this module is imported before any
# test module). Tests that care about a hermetic environment delete these via
# ``clean_env`` in test_config.py.
os.environ.setdefault("ADMIN_PASSWORD", "pw")
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("COOKIE_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


@pytest.fixture(autouse=True)
def no_dotenv_file(monkeypatch):
    """Keep all tests hermetic against a real repo-root ``.env`` file.

    ``Settings.from_env()``/``get_settings()`` load ``.env`` by default for the
    documented local-dev workflow, so a developer's real ``.env`` would leak
    into tests that assert on defaults or missing secrets. Every test module in
    this package therefore treats ``load_dotenv`` as a no-op, unless a
    test class overrides this fixture (same name) to exercise real ``.env``
    loading — see ``TestDotenvLoading`` in test_config.py.
    """
    import app.infrastructure.config as config

    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)


SECRET = "test-secret-key-0123456789abcdef"
PASSWORD = "admin-s3cret-pass"

VIDEO_URL = "https://www.bilibili.com/video/av123"


def make_settings(**overrides) -> Settings:
    """Build a ``Settings`` with the test secrets plus any overrides."""
    return Settings(
        admin_password=PASSWORD,
        secret_key=SECRET,
        cookie_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        **overrides,
    )


class FakeHub:
    """In-process stand-in for :class:`DownloadEventHub` that records events."""

    def __init__(self):
        self.events = []

    async def publish(self, download_id: str, event: dict) -> None:
        self.events.append((download_id, event))

    def for_download(self, download_id: str) -> list[dict]:
        return [event for did, event in self.events if did == download_id]


def seed_parse_task(
    engine, *, task_id=None, title="示例视频", media_type=MediaType.VIDEO,
    metadata=None,
) -> str:
    """Insert a ParseTask row; return its task_id."""
    task_id = task_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            ParseTask(
                task_id=task_id,
                url=VIDEO_URL,
                platform="bilibili",
                media_type=media_type,
                title=title,
                format="mp4",
                metadata_=metadata if metadata is not None else {},
            )
        )
    return task_id


def seed_download(
    engine,
    *,
    task_id,
    status=DownloadStatus.PENDING,
    download_id=None,
    retry_count=0,
    progress=0.0,
    **kwargs,
) -> str:
    """Insert a DownloadTask row; return its download_id."""
    download_id = download_id or str(uuid.uuid4())
    with session_scope(engine) as session:
        session.add(
            DownloadTask(
                download_id=download_id,
                task_id=task_id,
                title="示例视频",
                format=kwargs.pop("format", "mp4"),
                quality=kwargs.pop("quality", "1080p"),
                status=status,
                progress=progress,
                retry_count=retry_count,
                **kwargs,
            )
        )
    return download_id


def load_download(engine, download_id) -> DownloadTask:
    """Load a DownloadTask row (detached, attributes populated)."""
    with session_scope(engine) as session:
        return session.get(DownloadTask, download_id)


def expected_stub_bytes(download_id: str, title: str, total_bytes: int) -> bytes:
    """The deterministic byte stream the stub downloader writes (its contract).

    A SHA-256 digest of ``"<download_id>:<title>"`` repeated to exactly
    ``total_bytes`` — shared by the worker tests and the end-to-end smoke so
    the byte-level assertion cannot drift between the two suites.
    """
    digest = hashlib.sha256(f"{download_id}:{title}".encode("utf-8")).digest()
    return (digest * (total_bytes // len(digest) + 1))[:total_bytes]


@pytest.fixture
def env(tmp_path):
    """Worker-shaped test environment: (settings, engine, storage, token_provider).

    Uses a temp SQLite DB and temp bubble/pond roots so worker tests never
    touch the repository's ``data/`` tree.
    """
    settings = make_settings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        video_storage_path=tmp_path / "pond/video",
        image_storage_path=tmp_path / "pond/image",
        music_storage_path=tmp_path / "pond/music",
        temp_video_path=tmp_path / "bubble/video",
        temp_image_path=tmp_path / "bubble/image",
        temp_music_path=tmp_path / "bubble/music",
    )
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    assert seed.seed_admin(settings=settings, engine=engine) is True
    storage = get_storage(settings)
    token_provider = get_one_time_token_provider(settings)
    return settings, engine, storage, token_provider


@pytest.fixture
def engine(env):
    return env[1]


@pytest.fixture
def storage(env):
    return env[2]


@pytest.fixture
def token_provider(env):
    return env[3]
