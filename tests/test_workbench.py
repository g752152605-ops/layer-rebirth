import copy
import json
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from matplotlib.font_manager import FontProperties, findfont

from layer_rebirth.storage import ProjectStorage
from layer_rebirth.server import LayerRebirthService
from layer_rebirth.models import BoundingBox, Layer
from layer_rebirth.text_repair import prepare_text_repair


def fixture(tmp_path):
    image=Image.new("RGB",(360,180),"#f8f5ef")
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(findfont(FontProperties(family="Arial")),35)
    boxes=[]
    for text,y in [("Peter DiGangi",15),("Property Manager",60)]:
        draw.text((12,y),text,font=font,fill="black")
        x0,y0,x1,y1=draw.textbbox((12,y),text,font=font)
        boxes.append({"text":text,"score":.99,"box":[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]})
    service=LayerRebirthService(ProjectStorage(tmp_path/"data"),SimpleNamespace(status=lambda:{"licensed":True}))
    service.processor._recognize=lambda image:boxes
    buffer=BytesIO();image.save(buffer,"PNG")
    p=service.processor.process(buffer.getvalue(),"letters.png","marketing")
    return service,p,image


def test_original_glyph_move_and_revision_conflict(tmp_path):
    service,p,source=fixture(tmp_path)
    layer=p.layers[1]
    original=copy.deepcopy(layer.style["original_bbox"])
    layer.bbox.y+=60;layer.visible=True;layer.style["replacement_active"]=True
    result=service.update(p.id,{"project":p.to_dict(),"base_revision":0})
    assert result["project"]["revision"]==1
    moved=service.storage.load(p.id)
    assert moved.layers[1].style["original_bbox"]==original
    assert moved.layers[1].style["appearance"]=="original"
    with pytest.raises(ValueError,match="REVISION_CONFLICT"):
        service.update(p.id,{"project":p.to_dict(),"base_revision":0})
    layers=service.exporter.composition_layers(moved)
    glyph=next(l for l in layers if l.id==layer.id)
    assert glyph.kind=="raster" and glyph.asset.startswith("glyph-")
    patch=next(l for l in layers if l.id=="patch-"+layer.id)
    assert layers.index(patch)<layers.index(glyph)
    assets=service.drag_assets(p.id,layer.id,{"base_revision":1})
    assert assets["revision"]==1
    assert service.storage.asset(p.id,assets["object_url"].rsplit("/",1)[-1]).is_file()


def test_mask_versions_are_immutable_and_undo_restores_pixels(tmp_path):
    service,p,_=fixture(tmp_path)
    before=p.to_dict()
    layer=p.layers[1];old=layer.style["erase_asset"]
    directory=service.storage.project_dir(p.id);old_bytes=(directory/old).read_bytes()
    b=layer.bbox
    changed=service.repair(p.id,layer.id,{"base_revision":0,"strokes":[{"mode":"protect","radius":16,"points":[[b.x+20,b.y+15]]}]})
    new=changed["project"]["layers"][1]["style"]["erase_asset"]
    assert new!=old and (directory/old).read_bytes()==old_bytes
    restored=service.update(p.id,{"project":before,"base_revision":1})
    assert restored["project"]["layers"][1]["style"]["erase_asset"]==old


def test_real_overlapping_name_removes_descenders_preserves_neighbour(tmp_path):
    root=Path(__file__).parents[1]
    directory=root/"tmp/precision-qa/projects/5d5892a1-00f8-4bef-b745-322383a03cc9"
    if not directory.exists(): pytest.skip("real sample not installed")
    old=json.loads((directory/"project.layer.json").read_text(encoding="utf-8"))
    regions=[Layer(l["id"],l["name"],"text",BoundingBox(**l["bbox"]),content=l["content"],style={"original_content":l["content"]}) for l in old["layers"] if l["kind"]=="text"]
    layer=next(l for l in regions if "Peter" in l.content)
    source=Image.open(directory/"source.png").convert("RGBA")
    prepare_text_repair(layer,np.asarray(source),regions,tmp_path,match=False)
    b=layer.style["erase_bbox"];patch=Image.open(tmp_path/layer.style["erase_asset"])
    after=source.copy();after.alpha_composite(patch,(b["x"],b["y"]))
    pixels=np.asarray(after)
    # Bottom of original g overlaps the next OCR rectangle, but not its strokes.
    assert np.min(pixels[350:365,20:326,:3])>200
    assert np.array_equal(pixels[370:405,28:331],np.asarray(source)[370:405,28:331])


@pytest.mark.parametrize("background,foreground",[("white","black"),("black","white")])
def test_original_glyph_returns_to_origin_without_background_rectangle(tmp_path,background,foreground):
    image=Image.new("RGBA",(160,80),background)
    ImageDraw.Draw(image).text((15,15),"Move",font=ImageFont.truetype(findfont("Arial"),35),fill=foreground)
    layer=Layer("text-1","Move","text",BoundingBox(10,10,130,55),content="Move")
    prepare_text_repair(layer,np.asarray(image),[layer],tmp_path,match=False)
    b=layer.style["erase_bbox"]
    composed=image.copy()
    for key in ("erase_asset","glyph_asset"):
        composed.alpha_composite(Image.open(tmp_path/layer.style[key]),(b["x"],b["y"]))
    error=np.abs(np.asarray(composed).astype(int)-np.asarray(image).astype(int))
    assert error.max()<=6
    glyph=np.asarray(Image.open(tmp_path/layer.style["glyph_asset"]))
    assert np.mean(glyph[:,:,3]==0)>.6
