"""Strict domain contracts for previewable media manifests."""

import pytest
from pydantic import ValidationError

from app.domain import LivePhotoPair, MediaManifest, MediaResource


def resource(**overrides) -> MediaResource:
    values = {
        "url": "https://cdn.example/a.webp",
        "format": "webp",
        "width": 1920,
        "height": 1080,
        "quality": "original",
        "size_bytes": 0,
    }
    values.update(overrides)
    return MediaResource(**values)


def test_video_manifest_round_trips_with_stable_json_serialization():
    manifest = MediaManifest(
        kind="video",
        videos=[resource(url="https://cdn.example/a.mp4", format="mp4")],
        warnings=["quality metadata unavailable"],
    )

    dumped = manifest.model_dump(mode="json")
    loaded = MediaManifest.model_validate(dumped)

    assert loaded == manifest
    assert dumped == {
        "version": 1,
        "kind": "video",
        "videos": [
            {
                "url": "https://cdn.example/a.mp4",
                "format": "mp4",
                "width": 1920,
                "height": 1080,
                "quality": "original",
                "size_bytes": 0,
            }
        ],
        "images": [],
        "live_photos": [],
        "warnings": ["quality metadata unavailable"],
    }


def test_image_album_manifest_round_trips():
    manifest = MediaManifest(
        kind="image_album",
        images=[resource(), resource(url="https://cdn.example/b.png", format="png")],
    )

    assert MediaManifest.model_validate(manifest.model_dump()) == manifest


def test_live_photo_manifest_round_trips_with_optional_motion():
    manifest = MediaManifest(
        kind="live_photo",
        live_photos=[
            LivePhotoPair(
                image=resource(),
                motion=resource(url="https://cdn.example/a.mp4", format="mp4"),
            ),
            LivePhotoPair(image=resource(url="https://cdn.example/b.webp")),
        ],
        warnings=["1 张实况照片缺少动态视频"],
    )

    loaded = MediaManifest.model_validate(manifest.model_dump(mode="json"))

    assert loaded == manifest
    assert loaded.live_photos[1].motion is None
    assert loaded.warnings == ["1 张实况照片缺少动态视频"]


def test_resource_rejects_non_http_resource_url():
    for url in ("file:///etc/passwd", "ftp://cdn.example/a.mp4", "//cdn.example/a.mp4"):
        with pytest.raises(ValidationError):
            resource(url=url)


def test_resource_format_is_non_empty_and_at_most_16_characters():
    with pytest.raises(ValidationError):
        resource(format="")
    with pytest.raises(ValidationError):
        resource(format=" " * 16)
    with pytest.raises(ValidationError):
        resource(format="x" * 17)

    assert resource(format="x" * 16).format == "x" * 16


@pytest.mark.parametrize("field", ["width", "height"])
def test_resource_dimensions_must_be_positive(field):
    with pytest.raises(ValidationError):
        resource(**{field: 0})
    with pytest.raises(ValidationError):
        resource(**{field: -1})
    assert resource(**{field: 1}).model_dump()[field] == 1


def test_resource_size_bytes_must_be_non_negative():
    with pytest.raises(ValidationError):
        resource(size_bytes=-1)
    assert resource(size_bytes=0).size_bytes == 0


def test_resource_and_manifest_reject_extra_fields_and_are_frozen():
    with pytest.raises(ValidationError):
        resource(unexpected="field")
    with pytest.raises(ValidationError):
        MediaManifest(kind="video", videos=[resource()], unexpected="field")

    item = resource()
    with pytest.raises(ValidationError):
        item.format = "mp4"
    manifest = MediaManifest(kind="video", videos=[item])
    with pytest.raises(ValidationError):
        manifest.kind = "image_album"


def test_manifest_requires_exactly_one_non_empty_payload_matching_kind():
    cases = [
        {"kind": "video"},
        {"kind": "image_album"},
        {"kind": "live_photo"},
        {"kind": "video", "videos": [resource()], "images": [resource()]},
        {"kind": "image_album", "images": [resource()], "videos": [resource()]},
        {"kind": "live_photo", "live_photos": [LivePhotoPair(image=resource())], "images": [resource()]},
        {"kind": "video", "live_photos": [LivePhotoPair(image=resource())]},
    ]

    for values in cases:
        with pytest.raises(ValidationError):
            MediaManifest(**values)


def test_manifest_accepts_only_supported_kinds_and_version():
    with pytest.raises(ValidationError):
        MediaManifest(kind="audio", videos=[resource()])
    with pytest.raises(ValidationError):
        MediaManifest(kind="video", version=2, videos=[resource()])
