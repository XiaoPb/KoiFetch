"""Contract tests for prepared direct and staged transfers."""

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain import (
    AssetSelector,
    DirectTransfer,
    DownloadStatus,
    PrepareRequest,
    PreparedTransfer,
    StagedTransfer,
)


def test_asset_selector_accepts_single_assets_and_canonical_packages():
    for kind in ("video", "image", "live_image", "live_motion", "music"):
        selector = AssetSelector(kind=kind)
        assert selector.index == 0
        assert selector.package is None

    assert AssetSelector(kind="image", index=3).index == 3
    assert AssetSelector(kind="image", package="album_zip").package == "album_zip"
    assert AssetSelector(kind="live_image", package="live_zip").package == "live_zip"


def test_asset_selector_rejects_noncanonical_package_combinations():
    invalid = (
        {"kind": "video", "package": "album_zip"},
        {"kind": "music", "package": "album_zip"},
        {"kind": "image", "package": "live_zip"},
        {"kind": "live_motion", "package": "live_zip"},
        {"kind": "video", "package": "live_zip"},
        {"kind": "music", "package": "live_zip"},
        {"kind": "live_image", "package": "album_zip"},
        {"kind": "image", "package": "album_zip", "index": 1},
        {"kind": "live_image", "package": "live_zip", "index": 1},
        {"kind": "video", "package": "unknown"},
    )
    for payload in invalid:
        with pytest.raises(ValidationError):
            AssetSelector(**payload)


def test_asset_selector_is_strict_for_index_and_forbids_extra_fields():
    with pytest.raises(ValidationError):
        AssetSelector(kind="video", index=True)
    with pytest.raises(ValidationError):
        AssetSelector(kind="video", index="0")
    with pytest.raises(ValidationError):
        AssetSelector(kind="video", index=-1)
    with pytest.raises(ValidationError):
        AssetSelector(kind="video", unexpected="value")


def test_direct_transfer_requires_a_bounded_same_origin_api_path():
    transfer = DirectTransfer(
        url="/api/download/direct/task-1/asset?token=short-lived",
        filename="clip.mp4",
    )
    assert transfer.mode == "direct"

    invalid_urls = (
        "/api",
        "https://example.test/api/download/direct/task-1/asset",
        "//example.test/api/download/direct/task-1/asset",
        "/api/download/direct/../secret",
        "/api/download/direct/%2e%2e/secret",
        "/api/download/direct/%2fsecret",
        "/api/download/direct/%5csecret",
        "/api/download/direct/%252e%252e/secret",
        "/api/download/direct/task-1\x00/asset",
        "/api/download/direct/task-1/%2500/asset",
        "/api/download/direct/task-1/%ZZ/asset",
        "/download/direct/task-1/asset",
        "/api/download/direct/task-1/asset#fragment",
    )
    for url in invalid_urls:
        with pytest.raises(ValidationError):
            DirectTransfer(url=url, filename="clip.mp4")

    invalid_query_urls = (
        "/api/download/direct/task-1/asset?path=%2fsecret",
        "/api/download/direct/task-1/asset?path=%5csecret",
        "/api/download/direct/task-1/asset?path=%252e%252e%252fsecret",
        "/api/download/direct/task-1/asset?path=%00",
        "/api/download/direct/task-1/asset?path=%0d%0a",
        "/api/download/direct/task-1/asset?path=%ZZ",
    )
    for url in invalid_query_urls:
        with pytest.raises(ValidationError):
            DirectTransfer(url=url, filename="clip.mp4")


def test_direct_transfer_rejects_unsafe_or_unbounded_filenames():
    for filename in (
        "", " ", ".", "..", "../clip.mp4", r"..\clip.mp4", "a/b.mp4",
        "a\\b.mp4", "bad\nname.mp4", "x" * 256,
        "clip.", "clip ", "<clip>.mp4", "clip:name.mp4", "clip|name.mp4",
        "clip?name.mp4", "clip*name.mp4", "CON", "con.txt", "PRN.jpeg",
        "AUX.tar", "NUL.bin", "CLOCK$.txt", "COM1.mp4", "com9.zip", "LPT1.txt",
    ):
        with pytest.raises(ValidationError):
            DirectTransfer(
                url="/api/download/direct/task-1/asset",
                filename=filename,
            )


def test_staged_transfer_allows_active_statuses_but_not_terminal_failures():
    for status in (
        DownloadStatus.PENDING,
        DownloadStatus.DOWNLOADING,
        DownloadStatus.COMPLETED,
    ):
        transfer = StagedTransfer(download_id="download-1", status=status)
        assert transfer.status is status

    for status in (DownloadStatus.FAILED, DownloadStatus.EXPIRED):
        with pytest.raises(ValidationError):
            StagedTransfer(download_id="download-1", status=status)


def test_direct_transfer_forbids_extra_fields():
    with pytest.raises(ValidationError):
        DirectTransfer(
            url="/api/download/direct/task-1/asset",
            filename="clip.mp4",
            extra="x",
        )


def test_staged_transfer_requires_a_safe_bounded_id_and_forbids_extras():
    for download_id in ("", " ", "../download", "download/id", "x" * 129, "download id"):
        with pytest.raises(ValidationError):
            StagedTransfer(download_id=download_id, status="pending")
    with pytest.raises(ValidationError):
        StagedTransfer(download_id="download-1", status="pending", extra="x")


def test_staged_transfer_status_accepts_only_enum_or_exact_values():
    class StringLike(str):
        pass

    for status in ("pending", "downloading", "completed"):
        assert StagedTransfer(download_id="download-1", status=status).status is getattr(
            DownloadStatus, status.upper()
        )
    for status in (b"pending", 1, True, StringLike("pending"), " PENDING"):
        with pytest.raises(ValidationError):
            StagedTransfer(download_id="download-1", status=status)


def test_prepare_request_is_strict_frozen_and_round_trips_json_stably():
    request = PrepareRequest(
        task_id="task-1",
        asset=AssetSelector(kind="image", package="album_zip"),
        force_staged=False,
    )
    assert request.model_dump() == {
        "task_id": "task-1",
        "asset": {"kind": "image", "index": 0, "package": "album_zip"},
        "force_staged": False,
    }
    assert PrepareRequest.model_validate_json(request.model_dump_json()) == request

    with pytest.raises(ValidationError):
        PrepareRequest(task_id="task-1", asset=AssetSelector(kind="video"), force_staged=1)
    with pytest.raises(ValidationError):
        PrepareRequest(task_id="task-1", asset=AssetSelector(kind="video"), extra="x")
    with pytest.raises(ValidationError):
        PrepareRequest(task_id="../task", asset=AssetSelector(kind="video"))
    with pytest.raises(ValidationError):
        request.force_staged = True


@pytest.mark.parametrize(
    "value",
    [
        AssetSelector(kind="video"),
        DirectTransfer(
            url="/api/download/direct/task-1/asset",
            filename="clip.mp4",
        ),
        StagedTransfer(download_id="download-1", status="pending"),
    ],
)
def test_transfer_value_objects_are_immutable(value):
    field = next(iter(type(value).model_fields))
    with pytest.raises(ValidationError):
        setattr(value, field, value.model_dump()[field])


def test_prepared_transfer_is_discriminated_by_mode_and_round_trips():
    adapter = TypeAdapter(PreparedTransfer)
    direct = DirectTransfer(
        url="/api/download/direct/task-1/asset",
        filename="clip.mp4",
    )
    staged = StagedTransfer(download_id="download-1", status="pending")

    assert adapter.validate_python(direct.model_dump()) == direct
    assert adapter.validate_python(staged.model_dump()) == staged
    with pytest.raises(ValidationError):
        adapter.validate_python({"mode": "unknown", "url": "/api/x", "filename": "x"})


class _TransferEnvelope(BaseModel):
    transfer: PreparedTransfer


def test_prepared_transfer_can_be_embedded_in_a_pydantic_model():
    envelope = _TransferEnvelope(
        transfer={
            "mode": "staged",
            "download_id": "download-1",
            "status": "pending",
        }
    )
    assert isinstance(envelope.transfer, StagedTransfer)
