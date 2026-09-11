from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from layer_rebirth.models import BoundingBox, Layer, ProjectDocument, QualityReport
from layer_rebirth.exporting import ProjectExporter


def sample(tmp_path):
    Image.new("RGB", (240, 160), "#a02030").save(tmp_path / "source.png")
    text = Layer("text-1", "文字", "text", BoundingBox(20, 20, 160, 80), content="New text",
                 style={"font_family": "Arial", "font_size": 20, "fill": "#111111",
                        "replacement_active": True, "erase_mode": "solid", "erase_color": "#ffffff",
                        "erase_bbox": {"x": 10, "y": 10, "width": 180, "height": 100}})
    project = ProjectDocument("abc", "Sample", "marketing", "marketing",
                              {"width": 240, "height": 160}, {"asset": "source.png"},
                              [Layer("bg", "原图", "raster", BoundingBox(0, 0, 240, 160), asset="source.png"), text],
                              QualityReport("review", 80, {}))
    return project, text


@pytest.mark.parametrize("mode,expected", [("auto", (255, 255, 255)),
                                           ("transparent", (160, 32, 48)),
                                           ("solid", (0, 0, 255))])
def test_background_mode_affects_preview_and_both_svg_exports(tmp_path, mode, expected):
    project, text = sample(tmp_path)
    text.style.update(background_mode=mode, background_color="#0000ff")
    exporter = ProjectExporter()
    output = exporter.render_preview(project, tmp_path)
    with Image.open(output) as image:
        assert image.convert("RGB").getpixel((12, 12)) == expected
    for outlined in (False, True):
        svg = ET.fromstring(exporter._build_svg(project, tmp_path, outlined))
        rects = svg.findall(".//{http://www.w3.org/2000/svg}rect")
        assert len(rects) == (0 if mode == "transparent" else 1)
    text.style["erase_mode"] = "raster"
    text.style["erase_asset"] = "source.png"
    if mode == "transparent":
        assert exporter._svg_text_erase(text, tmp_path, "") == []


def test_full_typography_changes_render_and_survives_project_roundtrip(tmp_path):
    project, text = sample(tmp_path)
    exporter = ProjectExporter()
    path = exporter.render_preview(project, tmp_path)
    with Image.open(path) as image:
        before = np.asarray(image).copy()
    text.content = "Hello\nWorld"
    text.style.update(background_mode="transparent", font_size=24, font_weight="bold", font_style="italic",
                      underline=True, strikethrough=True, letter_spacing=2, line_height=1.4,
                      text_align="center", rotation=20, stroke_width=1.5, stroke_color="#00ff00", text_fit="actual")
    reopened = ProjectDocument.from_dict(project.to_dict())
    assert reopened.layers[1].style == text.style
    exporter.export_package(reopened, tmp_path, tmp_path / "exports", ["png", "svg", "pdf", "eps"])
    with Image.open(tmp_path / "exports" / "preview.png") as image:
        assert not np.array_equal(before, np.asarray(image))
    svg = ET.parse(tmp_path / "exports" / "editable.svg")
    texts = svg.findall(".//{http://www.w3.org/2000/svg}text")
    assert "".join(node.text or "" for node in texts) == "HelloWorld"
    assert len({node.get("y") for node in texts}) == 2
    assert all(node.get("font-weight") == "bold" and node.get("font-style") == "italic" for node in texts)
    assert all(node.get("stroke-width") == "1.5" for node in texts)
    assert b"<text" not in (tmp_path / "exports" / "outlined.svg").read_bytes()


def test_alignment_fixed_size_and_rotation_geometry(tmp_path):
    _, text = sample(tmp_path)
    exporter = ProjectExporter()
    text.style.update(text_fit="actual", font_size=24)
    left = exporter._fitted_text_path(text).vertices.copy()
    text.style["text_align"] = "right"
    right = exporter._fitted_text_path(text).vertices.copy()
    assert right[:, 0].min() > left[:, 0].min()
    assert np.ptp(left[:, 0]) == pytest.approx(np.ptp(right[:, 0]))
    text.style["rotation"] = 90
    rotated = exporter._fitted_text_path(text).vertices
    assert np.ptp(rotated[:, 1]) == pytest.approx(np.ptp(right[:, 0]))
    text.content = " "
    assert len(exporter._fitted_text_path(text).vertices) == 1


@pytest.mark.parametrize("key,value", [("font_size", float("nan")), ("line_height", 0),
                                       ("rotation", 999), ("background_mode", "bad")])
def test_invalid_text_parameters_are_rejected(tmp_path, key, value):
    project, text = sample(tmp_path)
    text.style[key] = value
    with pytest.raises(ValueError):
        ProjectDocument.from_dict(project.to_dict())
