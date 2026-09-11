from io import BytesIO

from PIL import Image

from layer_rebirth.server import LayerRebirthService
from layer_rebirth.storage import ProjectStorage


def test_prepare_batch_image_keeps_aspect_ratio_and_adds_background():
    source = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
    buffer = BytesIO()
    source.save(buffer, "PNG")

    result = LayerRebirthService._prepare_batch_image(
        buffer.getvalue(), width=300, height=300, background="white"
    )
    converted = Image.open(BytesIO(result)).convert("RGBA")

    assert converted.size == (300, 300)
    assert converted.getpixel((10, 10)) == (255, 255, 255, 255)
    assert converted.getpixel((150, 150)) == (255, 0, 0, 255)


def test_batch_dimension_rejects_oversized_value():
    try:
        LayerRebirthService._dimension(10001)
    except ValueError as exc:
        assert "10000" in str(exc)
    else:
        raise AssertionError("oversized batch dimension was accepted")


def test_storage_falls_back_when_primary_location_is_not_writable(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    fallback = tmp_path / "fallback"

    original_write_bytes = type(blocked).write_bytes

    def guarded_write(path, data):
        if path.parent == blocked:
            raise PermissionError("blocked for test")
        return original_write_bytes(path, data)

    monkeypatch.setattr(type(blocked), "write_bytes", guarded_write)
    selected = ProjectStorage._first_writable_root([blocked, fallback])

    assert selected == fallback
