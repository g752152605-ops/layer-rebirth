from dataclasses import asdict
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from matplotlib.font_manager import FontProperties, findfont

from layer_rebirth.models import Layer, BoundingBox
from layer_rebirth.text_repair import prepare_text_repair
from layer_rebirth.processing import ImageProcessor
from layer_rebirth.storage import ProjectStorage
from layer_rebirth.exporting import ProjectExporter


def source_and_layers():
    image = Image.new("RGBA", (420, 150), "#f5f2eb")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(findfont(FontProperties(family="Georgia", weight="bold")), 44)
    layers = []
    for i, (text, x, y) in enumerate([("Tavern", 20, 12), ("Main", 220, 70)]):
        draw.text((x, y), text, font=font, fill="black")
        a,b,c,d = draw.textbbox((x,y), text, font=font)
        layer = Layer(f"text-{i}", text, "text", BoundingBox(a,b,c-a,d-b), content=text,
                      style={"original_content":text,"original_bbox":{"x":a,"y":b,"width":c-a,"height":d-b}})
        layers.append(layer)
    draw.line((0,140,419,140), fill="black", width=2)
    return image, layers


def test_stroke_patch_removes_original_without_changing_neighbour_or_background(tmp_path):
    image, layers = source_and_layers()
    layer = layers[0]
    before = np.asarray(image)
    prepare_text_repair(layer, before, layers, tmp_path)
    box = layer.style["erase_bbox"]
    patch = Image.open(tmp_path / layer.style["erase_asset"]).convert("RGBA")
    assert 0 < np.count_nonzero(np.asarray(patch)[:,:,3]) < patch.width * patch.height * .65
    after = image.copy()
    after.alpha_composite(patch, (box["x"],box["y"]))
    a = np.asarray(after)
    neighbour = layers[1].bbox
    assert np.array_equal(a[neighbour.y:neighbour.y+neighbour.height, neighbour.x:neighbour.x+neighbour.width],
                          before[neighbour.y:neighbour.y+neighbour.height, neighbour.x:neighbour.x+neighbour.width])
    b=layer.bbox
    assert np.all(a[b.y:b.y+b.height,b.x:b.x+b.width,:3] == [245,242,235])
    assert np.array_equal(a[140:142],before[140:142])
    assert layer.style["font_weight"] == "bold"
    assert layer.style["font_family"] in {"Georgia", "Times New Roman"}


def test_manual_protection_preserves_overlapping_region(tmp_path):
    image, layers = source_and_layers()
    layers[1].bbox = BoundingBox(110,20,80,60)
    layers[1].style["original_bbox"] = asdict(layers[1].bbox)
    layers[0].style["repair_strokes"] = [{"mode":"protect","radius":40,"points":[[150,40],[150,60]]}]
    prepare_text_repair(layers[0], np.asarray(image), layers, tmp_path)
    patch = Image.open(tmp_path / layers[0].style["erase_asset"])
    box = layers[0].style["erase_bbox"]
    alpha = Image.new("L", image.size)
    alpha.paste(patch.getchannel("A"), (box["x"],box["y"]))
    assert not np.any(np.asarray(alpha)[20:80,120:180])
    assert layers[0].style["repair_version"] == 3


def test_edited_text_fits_and_export_contains_transparent_patch(tmp_path, monkeypatch):
    image, layers = source_and_layers()
    storage=ProjectStorage(tmp_path / "data")
    processor=ImageProcessor(storage)
    monkeypatch.setattr(processor,"_recognize",lambda image:[{"text":l.content,"score":.99,"box":[
        [l.bbox.x,l.bbox.y],[l.bbox.x+l.bbox.width,l.bbox.y],
        [l.bbox.x+l.bbox.width,l.bbox.y+l.bbox.height],[l.bbox.x,l.bbox.y+l.bbox.height]]} for l in layers])
    data=BytesIO(); image.save(data,"PNG")
    project=processor.process(data.getvalue(),"test.png","marketing")
    layer=project.layers[1]
    layer.content="Gao Yiming"
    layer.visible=True
    layer.style["replacement_active"]=True
    exporter=ProjectExporter()
    bounds=exporter._fitted_text_path(layer).get_extents()
    assert bounds.x0 >= layer.bbox.x-.01 and bounds.x1 <= layer.bbox.x+layer.bbox.width+.01
    assert bounds.y0 >= layer.bbox.y-.01 and bounds.y1 <= layer.bbox.y+layer.bbox.height+.01
    exporter.export_package(project,storage.project_dir(project.id),tmp_path / "out",["png","svg","pdf"])
    assert '<rect ' not in (tmp_path / "out/outlined.svg").read_text(encoding="utf-8")
    assert (tmp_path / "out/preview.png").is_file()


def test_old_project_repair_uses_original_position_and_preserves_user_font(tmp_path, monkeypatch):
    image, layers = source_and_layers()
    storage=ProjectStorage(tmp_path / "data")
    processor=ImageProcessor(storage)
    records=[{"text":l.content,"score":.99,"box":[[l.bbox.x,l.bbox.y],
        [l.bbox.x+l.bbox.width,l.bbox.y],[l.bbox.x+l.bbox.width,l.bbox.y+l.bbox.height],
        [l.bbox.x,l.bbox.y+l.bbox.height]]} for l in layers]
    monkeypatch.setattr(processor,"_recognize",lambda image: records)
    buffer=BytesIO(); image.save(buffer,"PNG")
    project=processor.process(buffer.getvalue(),"legacy.png","marketing")
    layer=project.layers[1]
    original=dict(layer.style["original_bbox"])
    for key in ("original_bbox","original_polygon","repair_version"):
        layer.style.pop(key)
    layer.bbox.x=300
    layer.style.update(replacement_active=True, font_family="Verdana")
    assert processor.upgrade_text_repairs(project)
    assert layer.style["original_bbox"] == original
    assert layer.bbox.x == 300
    assert layer.style["font_family"] == "Verdana"
    assert not processor.upgrade_text_repairs(project)
