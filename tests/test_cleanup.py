import base64
import copy
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from layer_rebirth.cleanup import cleanup_asset
from layer_rebirth.server import LayerRebirthService
from layer_rebirth.storage import ProjectStorage


def fixture_image():
    image = Image.new("RGBA", (240, 160), (245, 241, 233, 255))
    ImageDraw.Draw(image).rectangle((70, 60, 145, 82), fill=(70, 70, 70, 255))
    image.putpixel((95, 70), (90, 150, 50, 128))
    return image


def region(mode="erase"):
    return {"mode":mode,"shape":"rect","radius":4,"points":[[65,55],[150,87]]}


def test_cleanup_changes_only_mask_and_preserves_protected_alpha(tmp_path):
    image = fixture_image(); image.save(tmp_path / "source.png")
    protect = {"mode":"protect","shape":"brush","radius":3,"points":[[95,70]]}
    name = cleanup_asset(tmp_path, [region(), protect])
    before = np.array(image); after = np.array(Image.open(tmp_path/name))
    outside = np.ones(before.shape[:2], bool);outside[55:88,65:151]=False
    assert np.array_equal(before[outside],after[outside])
    assert np.array_equal(before[:,:,3],after[:,:,3])
    assert np.array_equal(before[70,95],after[70,95])
    assert np.mean(after[62:68,110:140,:3]) > 230
    assert name != "source.png"
    assert np.array_equal(np.array(Image.open(tmp_path/"source.png")),before)


def test_protection_has_priority_regardless_of_stroke_order(tmp_path):
    fixture_image().save(tmp_path/"source.png")
    protect = {"mode":"protect","shape":"rect","radius":4,"points":[[85,62],[110,78]]}
    assert cleanup_asset(tmp_path,[protect,region()]) == cleanup_asset(tmp_path,[region(),protect])


def test_flat_background_is_restored_without_a_bright_band(tmp_path):
    image = fixture_image(); image.save(tmp_path/'source.png')
    name = cleanup_asset(tmp_path,[region()])
    after = np.array(Image.open(tmp_path/name))
    assert np.all(after[55:88,65:151,:3] == [245,241,233])


def test_gradient_uses_local_inpainting_and_keeps_unmasked_pixels(tmp_path):
    pixels = np.zeros((160,240,4),np.uint8)
    pixels[:,:,:3] = np.arange(240,dtype=np.uint8)[None,:,None]
    pixels[:,:,3] = 255
    original = pixels.copy();pixels[60:83,70:146,:3]=30
    Image.fromarray(pixels).save(tmp_path/'source.png')
    name = cleanup_asset(tmp_path,[region()]);after=np.array(Image.open(tmp_path/name))
    assert np.array_equal(after[:55],pixels[:55])
    assert after[70,140,0] > after[70,75,0] + 30
    assert np.mean(np.abs(after[55:88,65:151,:3].astype(float)-original[55:88,65:151,:3])) < 12


@pytest.mark.parametrize("stroke", [
    {"mode":"erase","radius":float('nan'),"points":[[10,10]]},
    {"mode":"erase","radius":4,"points":[[-1,10]]},
    {"mode":"erase","shape":"rect","radius":4,"points":[[10,10]]},
])
def test_invalid_cleanup_does_not_write_assets(tmp_path, stroke):
    fixture_image().save(tmp_path/"source.png")
    with pytest.raises(ValueError): cleanup_asset(tmp_path,[stroke])
    assert not list(tmp_path.glob('cleanup-*.png'))


def test_cleanup_history_reopen_and_export(tmp_path):
    service = LayerRebirthService(ProjectStorage(tmp_path),SimpleNamespace(status=lambda:{"licensed":True}))
    raw = BytesIO();fixture_image().save(raw,"PNG")
    response = service.process({"file":{"name":"watermark.png","data":base64.b64encode(raw.getvalue()).decode()},"mode":"cleanup"})
    project = response["project"]; original = copy.deepcopy(project)
    assert project["mode"] == "cleanup" and service.processor._ocr is None
    repaired = service.cleanup(project["id"],{"base_revision":0,"strokes":[region()]})["project"]
    asset = repaired["layers"][0]["asset"]
    with pytest.raises(ValueError,match="REVISION_CONFLICT"):
        service.cleanup(project["id"],{"base_revision":0,"strokes":[]})
    restored = service.update(project["id"],{"base_revision":1,"project":original})["project"]
    assert restored["layers"][0]["asset"] == "source.png"
    redone = service.update(project["id"],{"base_revision":2,"project":repaired})["project"]
    assert redone["layers"][0]["asset"] == asset
    assert service.reopen(project["id"])["project"]["revision"] == 3
    exported = service.export(project["id"],{"formats":["png","svg","pdf","project"]})
    assert all(__import__('pathlib').Path(f["path"]).exists() for f in exported["files"])
    directory = service.storage.project_dir(project["id"])
    assert (directory/asset).is_file() and (directory/"source.png").is_file()
