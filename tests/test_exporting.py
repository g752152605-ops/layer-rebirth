import xml.etree.ElementTree as ET
from io import BytesIO

from PIL import Image

from layer_rebirth.exporting import ProjectExporter
from layer_rebirth.models import BoundingBox, Layer, ProjectDocument, QualityReport
from layer_rebirth.processing import ImageProcessor
from layer_rebirth.storage import ProjectStorage


def test_export_package_contains_editable_and_outlined_svg(tmp_path, monkeypatch):
    image = Image.new("RGB", (240, 120), "#f4ede1")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    storage = ProjectStorage(tmp_path / "data")
    processor = ImageProcessor(storage)
    monkeypatch.setattr(
        processor,
        "_recognize",
        lambda image: [{"box": [[20, 30], [210, 30], [210, 72], [20, 72]], "text": "SALE", "score": 0.97}],
    )
    project = processor.process(buffer.getvalue(), "sale.png", "marketing")
    text_layer = next(layer for layer in project.layers if layer.kind == "text")
    text_layer.style["appearance"] = "editable"
    text_layer.visible = True
    text_layer.style["replacement_active"] = True
    exporter = ProjectExporter()
    destination = tmp_path / "package"
    outputs = exporter.export_package(
        project,
        storage.project_dir(project.id),
        destination,
        ["svg", "pdf", "eps", "png", "report", "project"],
    )
    assert {path.name for path in outputs} == {
        "editable.svg", "outlined.svg", "print.pdf", "legacy.eps",
        "preview.png", "quality-report.html", "sale.layer.json",
    }
    ET.parse(destination / "editable.svg")
    ET.parse(destination / "outlined.svg")
    assert "<text" in (destination / "editable.svg").read_text(encoding="utf-8")
    assert 'textLength=' not in (destination / "editable.svg").read_text(encoding="utf-8")
    assert "<text" not in (destination / "outlined.svg").read_text(encoding="utf-8")
    assert (destination / "print.pdf").read_bytes().startswith(b"%PDF")
    assert (destination / "legacy.eps").read_bytes().startswith(b"%!PS-Adobe")


def test_vector_color_override_preserves_white_knockouts(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "vector.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<path fill="#101010" d="M0 0L20 0L20 20Z"/>'
        '<path fill="#ffffff" d="M4 4L8 4L8 8Z"/>'
        '</svg>',
        encoding="utf-8",
    )
    project = ProjectDocument(
        id="project", name="logo", mode="logo", recommended_mode="logo",
        canvas={"width": 20, "height": 20, "unit": "px", "background": "transparent"},
        source={"asset": "source.png", "filename": "source.png"},
        layers=[Layer(
            id="vector-1", name="矢量主体", kind="vector",
            bbox=BoundingBox(0, 0, 20, 20), asset="vector.svg",
            style={"fill_override": "#d35332"},
        )],
        quality=QualityReport("ready", 90, {}),
    )
    result = ProjectExporter()._build_svg(project, project_dir, outlined=False)
    assert 'fill="#d35332"' in result
    assert 'fill="#ffffff"' in result


def test_fitted_text_path_stays_inside_ocr_box():
    layer = Layer(
        id="text-1", name="文字", kind="text",
        bbox=BoundingBox(40, 20, 120, 36), content="VERY WIDE TEXT",
        style={"font_family": "Arial", "font_size": 28, "fill": "#000000"},
    )
    path = ProjectExporter()._fitted_text_path(layer)
    assert path.vertices[:, 0].min() >= 40
    assert path.vertices[:, 0].max() <= 160.001


def test_active_candidate_erases_original_area_before_drawing_text(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "background.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path fill="#000000" d="M0 0L200 0L200 80L0 80Z"/></svg>',
        encoding="utf-8",
    )
    candidate = Layer(
        id="text-1", name="候选文字 1", kind="text",
        bbox=BoundingBox(20, 20, 120, 30), content="NEW TEXT", visible=True,
        style={
            "font_family": "Arial", "font_size": 24, "fill": "#000000",
            "candidate_only": True, "replacement_active": True,
            "original_content": "OLD TEXT", "erase_mode": "solid",
            "erase_color": "#ffffff",
            "erase_bbox": {"x": 16, "y": 16, "width": 128, "height": 38},
        },
    )
    project = ProjectDocument(
        id="project", name="replace", mode="marketing", recommended_mode="marketing",
        canvas={"width": 200, "height": 80, "unit": "px", "background": "transparent"},
        source={"asset": "source.png", "filename": "source.png"},
        layers=[
            Layer("background", "原稿", "vector", BoundingBox(0, 0, 200, 80), asset="background.svg"),
            candidate,
        ],
        quality=QualityReport("review", 78, {}),
    )

    result = ProjectExporter()._build_svg(project, project_dir, outlined=False)

    assert '<rect x="16.00" y="16.00" width="128.00" height="38.00" fill="#ffffff"/>' in result
    assert result.index("<rect") < result.index("<text")
    assert "NEW TEXT" in result
    assert "OLD TEXT" not in result
