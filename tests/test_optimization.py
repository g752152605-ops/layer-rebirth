from io import BytesIO
import re

import numpy as np
import pytest
from PIL import Image, ImageDraw
from matplotlib.textpath import TextPath

from layer_rebirth.exporting import ProjectExporter
from layer_rebirth.models import BoundingBox, Layer
from layer_rebirth.processing import ImageProcessor
from layer_rebirth.storage import ProjectStorage
from layer_rebirth.server import create_server
from pathlib import Path
import json
import threading
from urllib.request import urlopen, Request


def raster_project(tmp_path):
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((60, 30, 140, 70), fill="red")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    storage = ProjectStorage(tmp_path / "data")
    project = ImageProcessor(storage).process(buffer.getvalue(), "transparent.png", "format")
    return storage, project


@pytest.mark.parametrize("dpi", [72, 144, 300])
@pytest.mark.parametrize("format_name", ["png", "webp", "jpg"])
def test_raster_export_preserves_pixels_and_alpha(tmp_path, dpi, format_name):
    storage, project = raster_project(tmp_path)
    project.export_settings["dpi"] = dpi
    output = tmp_path / "export"
    ProjectExporter().export_package(project, storage.project_dir(project.id), output, [format_name])
    with Image.open(output / f"preview.{format_name}") as image:
        assert image.size == (200, 100)
        corner = image.convert("RGBA").getpixel((0, 0))
        assert corner[3] == (255 if format_name == "jpg" else 0)
        if format_name != "webp":
            assert image.info["dpi"][0] == pytest.approx(dpi, abs=1)


def test_pdf_physical_size_uses_dpi(tmp_path):
    storage, project = raster_project(tmp_path)
    project.export_settings["dpi"] = 200
    output = tmp_path / "export"
    ProjectExporter().export_package(project, storage.project_dir(project.id), output, ["pdf"])
    page = re.search(rb"/MediaBox\s*\[([^]]+)\]", (output / "print.pdf").read_bytes())
    assert [float(x) for x in page[1].split()] == pytest.approx([0, 0, 72, 36])


def test_text_fit_preserves_aspect_ratio_and_does_not_enlarge():
    exporter = ProjectExporter()
    for content in ["HI", "A VERY LONG PRODUCT DESCRIPTION"]:
        layer = Layer("text-1", "Text", "text", BoundingBox(20, 20, 120, 36),
                      content=content, style={"font_family": "Arial", "font_size": 28})
        original = TextPath((0, 0), content, size=28, prop=exporter._font(layer)).vertices
        result = exporter._fitted_text_path(layer).vertices
        scale = np.ptp(result, axis=0) / np.ptp(original, axis=0)
        assert scale[0] == pytest.approx(scale[1])
        assert scale[0] <= 1
        assert result[:, 0].max() <= 140.001


def test_marketing_preserves_original_until_local_edit(tmp_path, monkeypatch):
    storage = ProjectStorage(tmp_path / "data")
    processor = ImageProcessor(storage)
    image = Image.new("RGB", (200, 100), "#e0c8a0")
    ImageDraw.Draw(image).text((20, 30), "OLD", fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    monkeypatch.setattr(processor, "_recognize", lambda image: [
        {"box": [[18, 28], [60, 28], [60, 48], [18, 48]], "text": "OLD", "score": .99}
    ])
    project = processor.process(buffer.getvalue(), "poster.png", "marketing")
    layer = project.layers[1]
    assert not layer.visible and layer.style["candidate_only"]
    exporter = ProjectExporter()
    path = exporter.render_preview(project, storage.project_dir(project.id))
    original = np.asarray(Image.open(path).convert("RGB")).copy()
    assert np.array_equal(original, np.asarray(image))
    layer.visible = True
    layer.style["replacement_active"] = True
    layer.content = "NEW"
    exporter.render_preview(project, storage.project_dir(project.id))
    changed = np.asarray(Image.open(path).convert("RGB"))
    bbox = layer.style["erase_bbox"]
    outside = np.ones((100, 200), dtype=bool)
    x, y, w, h = (int(bbox[k]) for k in ["x", "y", "width", "height"])
    outside[y:y+h, x:x+w] = False
    assert np.array_equal(original[outside], changed[outside])
    assert not np.array_equal(original, changed)
    layer.visible = False
    layer.style["replacement_active"] = False
    exporter.render_preview(project, storage.project_dir(project.id))
    assert np.array_equal(original, np.asarray(Image.open(path).convert("RGB")))


def test_recent_projects_reopens_saved_edits_over_http(tmp_path):
    storage, project = raster_project(tmp_path)
    server = create_server("127.0.0.1", 0, Path(__file__).resolve().parents[1] / "web", storage.root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/projects") as response:
            assert json.load(response)["projects"][0]["id"] == project.id
        project.name = "Continued project"
        project.layers[0].opacity = .5
        request = Request(base + f"/api/projects/{project.id}",
                          data=json.dumps({"project": project.to_dict()}).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            assert json.load(response)["ok"]
        with urlopen(base + f"/api/projects/{project.id}") as response:
            reopened = json.load(response)["project"]
        assert reopened["name"] == "Continued project"
        assert reopened["layers"][0]["opacity"] == .5
        (storage.projects / "bad").mkdir()
        (storage.projects / "bad" / "project.layer.json").write_text("broken")
        assert len(storage.recent()) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
