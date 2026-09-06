from datetime import date

from app.application.nas_path import build_media_pond_path


def test_builds_platform_date_author_work_and_filename_path():
    assert build_media_pond_path(
        media_bucket="image",
        platform="douyin",
        published_at="2026-09-06",
        author="作者A",
        work_id="7123456789",
        title="作品标题",
        extension="webp",
        index=0,
    ) == "douyin/2026-09-06/作者a/7123456789/作者a_作品标题_001.webp"


def test_uses_download_date_and_task_id_fallbacks():
    assert build_media_pond_path(
        media_bucket="video",
        platform=None,
        published_at=None,
        author=None,
        work_id=None,
        task_id="task-123",
        title=None,
        extension="mp4",
        downloaded_on=date(2026, 9, 6),
    ) == "unknown-platform/2026-09-06/unknown-author/task-123/unknown-author_untitled.mp4"


def test_live_motion_uses_the_same_work_directory_without_image_index():
    assert build_media_pond_path(
        media_bucket="video",
        platform="douyin",
        published_at="2026-09-06",
        author="作者A",
        work_id="7123456789",
        title="作品标题",
        extension="mp4",
        resource_kind="live_motion",
    ) == "douyin/2026-09-06/作者a/7123456789/作者a_作品标题_motion.mp4"


def test_rejects_invalid_extension_and_normalizes_dynamic_segments():
    path = build_media_pond_path(
        media_bucket="image",
        platform="douyin/../微博",
        published_at="2026-09-06",
        author="作者/甲",
        work_id="../id",
        title="标题/一",
        extension=".WEBP",
        index=1,
    )
    assert path == "douyin-微博/2026-09-06/作者-甲/id/作者-甲_标题-一_002.webp"
