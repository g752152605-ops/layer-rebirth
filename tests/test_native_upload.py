from pathlib import Path

from PIL import Image

import run_app

from run_app import encode_image_paths


def test_encode_image_paths_returns_browser_ready_payload(tmp_path: Path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (4, 3), "white").save(image_path)

    result = encode_image_paths([str(image_path)])

    assert len(result) == 1
    assert result[0]["name"] == "sample.png"
    assert result[0]["type"] == "image/png"
    assert result[0]["size"] == image_path.stat().st_size
    assert str(result[0]["data"]).startswith("data:image/png;base64,")


def test_encode_image_paths_skips_unsupported_or_oversized_files(tmp_path: Path, monkeypatch):
    text_path = tmp_path / "sample.txt"
    text_path.write_text("not an image", encoding="utf-8")
    image_path = tmp_path / "large.jpg"
    image_path.write_bytes(b"x")

    monkeypatch.setattr(run_app, "MAX_IMAGE_BYTES", 0)
    assert encode_image_paths([str(text_path), str(image_path)]) == []


def test_native_api_opens_windows_image_picker(tmp_path: Path):
    image_path = tmp_path / "picked.png"
    Image.new("RGB", (2, 2), "black").save(image_path)

    class FakeWindow:
        def __init__(self):
            self.arguments = None

        def create_file_dialog(self, *args, **kwargs):
            self.arguments = (args, kwargs)
            return (str(image_path),)

    window = FakeWindow()
    api = run_app.NativeApi()
    api._window = window

    result = api.choose_images()

    assert len(result) == 1
    assert result[0]["name"] == "picked.png"
    assert window.arguments[1]["allow_multiple"] is True
    assert "*.png" in window.arguments[1]["file_types"][0]


def test_upload_control_is_a_direct_file_input():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")

    file_input = html.split('id="fileInput"', 1)[1].split(">", 1)[0]
    assert 'type="file"' in file_input
    assert "hidden" not in file_input
    assert 'aria-label="选择图片"' in file_input
    assert 'id="chooseFileButton"' not in html
