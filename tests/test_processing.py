from io import BytesIO

from PIL import Image, ImageDraw

from layer_rebirth.processing import ImageProcessor
from layer_rebirth.storage import ProjectStorage


def sample_image() -> bytes:
    image = Image.new("RGB", (360, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, 342, 162), outline="#20201d", width=5)
    draw.rectangle((42, 72, 318, 128), fill="#d35332")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_logo_processing_creates_real_vector(tmp_path):
    storage = ProjectStorage(tmp_path)
    project = ImageProcessor(storage).process(sample_image(), "logo.png", "logo")
    vector = storage.project_dir(project.id) / "vector.svg"
    assert vector.exists()
    assert "<path" in vector.read_text(encoding="utf-8")
    assert project.layers[0].kind == "vector"
    assert project.quality.metrics["node_count"] > 0


def test_marketing_processing_exposes_editable_text(tmp_path, monkeypatch):
    storage = ProjectStorage(tmp_path)
    processor = ImageProcessor(storage)
    monkeypatch.setattr(
        processor,
        "_recognize",
        lambda image: [
            {
                "box": [[45, 75], [310, 75], [310, 124], [45, 124]],
                "text": "新品上架",
                "score": 0.84,
            }
        ],
    )
    project = processor.process(sample_image(), "poster.png", "marketing")
    text = next(layer for layer in project.layers if layer.kind == "text")
    assert text.content == "新品上架"
    assert text.confidence == 0.84
    assert project.quality.verdict in {"review", "refine"}
    assert any("置信度" in warning for warning in project.quality.warnings)


def test_rejects_unsupported_input(tmp_path):
    processor = ImageProcessor(ProjectStorage(tmp_path))
    try:
        processor.process(b"not-an-image", "bad.txt")
    except ValueError as exc:
        assert "损坏" in str(exc)
    else:
        raise AssertionError("invalid input was accepted")


def test_monochrome_line_art_is_recommended_as_logo(tmp_path, monkeypatch):
    storage = ProjectStorage(tmp_path)
    processor = ImageProcessor(storage)
    monkeypatch.setattr(
        processor,
        "_analyze",
        lambda image: {"color_count": 50, "edge_density": 0.21, "grayscale_ratio": 1.0},
    )
    project = processor.process(sample_image(), "logo-sheet.png", "auto")
    assert project.mode == "logo"
    assert project.recommended_mode == "logo"


def test_dense_marketing_layout_uses_fidelity_protection(tmp_path, monkeypatch):
    storage = ProjectStorage(tmp_path)
    processor = ImageProcessor(storage)
    monkeypatch.setattr(
        processor,
        "_analyze",
        lambda image: {"color_count": 50, "edge_density": 0.21, "grayscale_ratio": 1.0},
    )
    monkeypatch.setattr(
        processor,
        "_recognize",
        lambda image: [
            {
                "box": [[10, index * 8], [140, index * 8], [140, index * 8 + 7], [10, index * 8 + 7]],
                "text": f"LINE {index}",
                "score": 0.98,
            }
            for index in range(18)
        ],
    )
    project = processor.process(sample_image(), "logo-sheet.png", "marketing")
    assert project.layers[0].kind == "raster"
    assert project.layers[0].asset == "source.png"
    assert all(not layer.visible for layer in project.layers[1:])
    assert all(layer.style["candidate_only"] for layer in project.layers[1:])
    assert all(not layer.style["replacement_active"] for layer in project.layers[1:])
    assert all(layer.style["original_content"] == layer.content for layer in project.layers[1:])
    assert all(layer.style["erase_mode"] in {"solid", "raster"} for layer in project.layers[1:])
    assert all(layer.style["repair_version"] == 3 for layer in project.layers[1:])
