"""Strict domain contracts for previewable media manifests."""

import json

import pytest
from pydantic import ValidationError

from app.application.media_manifest import public_manifest
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
        videos=(resource(url="https://cdn.example/a.mp4", format="mp4"),),
        warnings=("quality metadata unavailable",),
    )

    dumped = manifest.model_dump(mode="json")
    loaded = MediaManifest.model_validate_json(manifest.model_dump_json())

    assert loaded == manifest
    assert isinstance(dumped["videos"], list)
    assert isinstance(dumped["warnings"], list)
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
        images=(
            resource(),
            resource(url="https://cdn.example/b.png", format="png"),
        ),
    )

    assert MediaManifest.model_validate(manifest.model_dump()) == manifest


def test_live_photo_manifest_round_trips_with_optional_motion():
    manifest = MediaManifest(
        kind="live_photo",
        live_photos=(
            LivePhotoPair(
                image=resource(),
                motion=resource(url="https://cdn.example/a.mp4", format="mp4"),
            ),
            LivePhotoPair(image=resource(url="https://cdn.example/b.webp")),
        ),
        warnings=("1 张实况照片缺少动态视频",),
    )

    loaded = MediaManifest.model_validate_json(manifest.model_dump_json())

    assert loaded == manifest
    assert loaded.live_photos[1].motion is None
    assert loaded.warnings == ("1 张实况照片缺少动态视频",)
    assert isinstance(manifest.model_dump(mode="json")["live_photos"], list)


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
    for invalid in (
        " mp4",
        "mp 4",
        "mp4/part",
        "mp4\\part",
        "mp4\n",
    ):
        with pytest.raises(ValidationError):
            resource(format=invalid)

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


def test_resource_quality_has_a_bounded_clean_value():
    with pytest.raises(ValidationError):
        resource(quality="")
    with pytest.raises(ValidationError):
        resource(quality="x" * 65)
    for invalid in ("1080p\n", "1080p\t", "1080p\x00"):
        with pytest.raises(ValidationError):
            resource(quality=invalid)

    assert resource(quality="x" * 64).quality == "x" * 64


def test_manifest_warnings_are_bounded_and_clean():
    with pytest.raises(ValidationError):
        MediaManifest(
            kind="video", videos=(resource(),), warnings=("x" * 257,)
        )
    with pytest.raises(ValidationError):
        MediaManifest(kind="video", videos=(resource(),), warnings=("warning\n",))
    with pytest.raises(ValidationError):
        MediaManifest(
            kind="video",
            videos=(resource(),),
            warnings=tuple("warning" for _ in range(33)),
        )

    manifest = MediaManifest(
        kind="video",
        videos=(resource(),),
        warnings=tuple("warning" for _ in range(32)),
    )
    assert len(manifest.warnings) == 32


def test_manifest_accepts_list_payloads_and_normalizes_them_to_tuples():
    manifest = MediaManifest(
        kind="video",
        videos=[resource()],
        warnings=["warning"],
    )

    assert isinstance(manifest.videos, tuple)
    assert isinstance(manifest.warnings, tuple)


def test_manifest_validates_sqlalchemy_json_list_payloads():
    manifest = MediaManifest(kind="video", videos=(resource(),))

    loaded = MediaManifest.model_validate(manifest.model_dump(mode="json"))

    assert loaded == manifest
    assert isinstance(loaded.videos, tuple)


@pytest.mark.parametrize(
    ("field", "kind", "item"),
    [
        ("videos", "video", resource()),
        ("images", "image_album", resource()),
        ("live_photos", "live_photo", LivePhotoPair(image=resource())),
        ("warnings", "video", "warning"),
    ],
)
def test_manifest_rejects_non_list_or_tuple_collection_inputs(field, kind, item):
    with pytest.raises(ValidationError):
        MediaManifest(kind=kind, **{field: {item}})
    with pytest.raises(ValidationError):
        MediaManifest(kind=kind, **{field: (value for value in [item])})
    with pytest.raises(ValidationError):
        MediaManifest(kind=kind, **{field: "not-a-collection"})


def test_list_boundary_keeps_nested_elements_strict():
    with pytest.raises(ValidationError):
        MediaManifest(
            kind="video",
            videos=[
                {
                    "url": "https://cdn.example/a.mp4",
                    "format": "mp4",
                    "width": "1920",
                }
            ],
        )


@pytest.mark.parametrize("field", ["width", "height", "size_bytes"])
@pytest.mark.parametrize("value", ["1", 1.0, True])
def test_resource_numeric_fields_reject_coercion(field, value):
    with pytest.raises(ValidationError):
        resource(**{field: value})


def test_manifest_contracts_enable_strict_validation():
    assert MediaResource.model_config["strict"] is True
    assert LivePhotoPair.model_config["strict"] is True
    assert MediaManifest.model_config["strict"] is True


def test_resource_and_manifest_reject_extra_fields_and_are_frozen():
    with pytest.raises(ValidationError):
        resource(unexpected="field")
    with pytest.raises(ValidationError):
        MediaManifest(kind="video", videos=(resource(),), unexpected="field")

    item = resource()
    with pytest.raises(ValidationError):
        item.format = "mp4"
    manifest = MediaManifest(kind="video", videos=(item,))
    with pytest.raises(ValidationError):
        manifest.kind = "image_album"


def test_manifest_collections_and_live_photo_pairs_are_deeply_frozen():
    pair = LivePhotoPair(image=resource())
    manifest = MediaManifest(kind="live_photo", live_photos=(pair,))

    with pytest.raises(AttributeError):
        manifest.live_photos.append(pair)
    with pytest.raises(TypeError):
        manifest.live_photos[0] = pair
    with pytest.raises(ValidationError):
        pair.image = resource(format="jpg")
    with pytest.raises(ValidationError):
        LivePhotoPair(image=resource(), unexpected="field")


def test_manifest_requires_exactly_one_non_empty_payload_matching_kind():
    cases = [
        {"kind": "video"},
        {"kind": "image_album"},
        {"kind": "live_photo"},
        {"kind": "video", "videos": (resource(),), "images": (resource(),)},
        {"kind": "image_album", "images": (resource(),), "videos": (resource(),)},
        {
            "kind": "live_photo",
            "live_photos": (LivePhotoPair(image=resource()),),
            "images": (resource(),),
        },
        {"kind": "video", "live_photos": (LivePhotoPair(image=resource()),)},
    ]

    for values in cases:
        with pytest.raises(ValidationError):
            MediaManifest(**values)


def test_manifest_accepts_only_supported_kinds_and_version():
    with pytest.raises(ValidationError):
        MediaManifest(kind="audio", videos=(resource(),))
    with pytest.raises(ValidationError):
        MediaManifest(kind="video", version=2, videos=(resource(),))


def test_public_manifest_preserves_safe_quality_and_warning_text():
    video_manifest = MediaManifest(
        kind="video",
        videos=(resource(quality="1080p"),),
    )
    live_manifest = MediaManifest(
        kind="live_photo",
        live_photos=(LivePhotoPair(image=resource()),),
        warnings=("quality metadata unavailable",),
    )

    video_public = public_manifest("task-1", video_manifest)
    live_public = public_manifest("task-1", live_manifest)

    assert video_public["videos"][0]["quality"] == "1080p"
    assert live_public["warnings"] == ["quality metadata unavailable"]


def test_public_manifest_redacts_url_bearing_quality_and_warning_as_whole_values():
    secret = "https://cdn.example/private-token?sig=secret"
    video_manifest = MediaManifest(
        kind="video",
        videos=(resource(quality=f"best {secret}"),),
    )
    live_manifest = MediaManifest(
        kind="live_photo",
        live_photos=(LivePhotoPair(image=resource()),),
        warnings=(f"failed to load {secret}",),
    )

    video_public = public_manifest("task-1", video_manifest)
    live_public = public_manifest("task-1", live_manifest)
    serialized = json.dumps([video_public, live_public])

    assert "cdn.example" not in serialized
    assert "private-token" not in serialized
    assert "sig=secret" not in serialized
    assert video_public["videos"][0]["quality"] == "metadata unavailable"
    assert live_public["warnings"] == ["metadata unavailable"]
