import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
import webview

from run_app import NativeApi
from layer_rebirth.server import LayerRebirthService
from layer_rebirth.storage import ProjectStorage


def test_native_folder_picker_and_cancel(tmp_path):
    calls = []
    api = NativeApi()
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: calls.append((a, kw)) or (str(tmp_path),))
    assert api.choose_export_directory(str(tmp_path)) == str(tmp_path)
    assert calls[0][0] == (webview.FileDialog.FOLDER,)
    assert calls[0][1] == {"directory": str(tmp_path), "allow_multiple": False}
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: None)
    assert api.choose_export_directory() is None


def test_export_and_batch_use_custom_directory_and_remember(tmp_path):
    storage = ProjectStorage(tmp_path / "data")
    service = LayerRebirthService(storage, SimpleNamespace(status=lambda: {"licensed": True}))
    buffer = BytesIO()
    Image.new("RGB", (30, 20), "red").save(buffer, "PNG")
    project = service.processor.process(buffer.getvalue(), "sample.png", "format")
    target = tmp_path / "中文 导出" / "新文件夹"
    result = service.export(project.id, {"formats": ["png"], "destination": str(target)})
    assert Path(result["destination"]).parent == target
    assert all(Path(file["path"]).is_file() for file in result["files"])
    assert ProjectStorage(storage.root).export_directory() == str(target)
    second = service.export(project.id, {"formats": ["png"], "destination": str(target)})
    assert second["destination"] != result["destination"]
    batch = service.batch({"files": [{"name": "sample.png", "data": base64.b64encode(buffer.getvalue()).decode()}], "destination": str(target)})
    assert all(Path(file).parent.parent == target and Path(file).is_file()
               for item in batch["results"] for file in item["files"])
    default = service.export(project.id, {"formats": ["png"]})
    assert Path(default["destination"]).parent == storage.exports


@pytest.mark.parametrize("value", ["", "relative/path", 123])
def test_invalid_directory_rejected(tmp_path, value):
    storage = ProjectStorage(tmp_path / "data")
    with pytest.raises(ValueError, match="完整绝对路径"):
        storage.prepare_export_directory(value)


def test_file_cannot_be_export_directory(tmp_path):
    storage = ProjectStorage(tmp_path / "data")
    target = tmp_path / "file.txt"
    target.write_text("keep")
    with pytest.raises(ValueError, match="无法写入"):
        storage.prepare_export_directory(str(target))
    assert target.read_text() == "keep"
    assert storage.export_directory() == str(storage.exports)

def test_all_exports_and_batch_available_without_license(tmp_path, monkeypatch):
    storage = ProjectStorage(tmp_path / 'data')
    service = LayerRebirthService(storage, SimpleNamespace(status=lambda: {'licensed': False}))
    buffer = BytesIO()
    Image.new('RGB', (30,20), 'red').save(buffer, 'PNG')
    project = service.processor.process(buffer.getvalue(), 'sample.png', 'format')
    exporter = service.exporter
    original = exporter.export_package
    watermarks = []
    def capture(*args, **kwargs):
        watermarks.append(kwargs.get('watermark', False))
        return original(*args, **kwargs)
    monkeypatch.setattr(exporter, 'export_package', capture)
    result = service.export(project.id, {'formats':['svg','pdf','eps','png','report','project']})
    names = [file['name'] for file in result['files']]
    assert any(name.endswith('.svg') for name in names)
    assert any(name.endswith('.pdf') for name in names)
    assert any(name.endswith('.eps') for name in names)
    assert any(name.endswith('.json') for name in names)
    assert result['trial_limited'] is False
    item = {'name':'sample.png','data':base64.b64encode(buffer.getvalue()).decode()}
    assert service.batch({'files':[item,item], 'width':60, 'height':40})['count'] == 2
    assert watermarks == [False, False, False]
