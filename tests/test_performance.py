from io import BytesIO
import json
import os
import threading
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from PIL import Image
import matplotlib.pyplot as plt

import layer_rebirth.worker as worker_module
from layer_rebirth.worker import ProcessingWorker, TaskBusy
from layer_rebirth.models import BoundingBox, Layer, ProjectDocument, QualityReport
from layer_rebirth.exporting import ProjectExporter
from layer_rebirth.server import create_server


def slow_worker(connection, root, public_key):
    """Deterministic test substitute for a slow/crashing native library."""
    while True:
        operation, request, result = connection.recv()
        options = json.loads(Path(request).read_text())[0]
        if options.get("crash"):
            os._exit(7)
        time.sleep(options.get("delay", 0))
        Path(result).write_text(json.dumps({"value": options.get("value", 1)}))
        connection.send((True, ""))


def test_worker_timeout_crash_cancel_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "_serve", slow_worker)
    worker = ProcessingWorker(tmp_path, tmp_path / "unused.pem", timeout=10)
    try:
        assert worker.run("process", {}) == {"value": 1}
        worker.timeout = .3
        with pytest.raises(TimeoutError):
            worker.run("process", {"delay": 30})
        worker.timeout = 10
        assert worker.run("process", {"value": 2}) == {"value": 2}
        with pytest.raises(ValueError, match="异常退出"):
            worker.run("process", {"crash": True})
        assert worker.run("process", {}) == {"value": 1}
        errors = []
        def run_slow():
            try:
                worker.run("process", {"delay": 30})
            except ValueError as error:
                errors.append(str(error))
        thread = threading.Thread(target=run_slow)
        thread.start()
        for _ in range(100):
            if worker.operation:
                break
            time.sleep(.01)
        with pytest.raises(TaskBusy):
            worker.run("export", {})
        worker.cancel()
        thread.join(timeout=5)
        assert not thread.is_alive() and "取消" in errors[0]
        assert worker.run("process", {"value": 3}) == {"value": 3}
    finally:
        worker.close()


def test_health_responds_while_worker_is_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "_serve", slow_worker)
    server = create_server("127.0.0.1", 0, Path(__file__).resolve().parents[1] / "web", tmp_path)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    errors = []
    def run_slow():
        try:
            server.worker.run("process", {"delay": 30})
        except ValueError as error:
            errors.append(str(error))
    thread = threading.Thread(target=run_slow)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=3) as response:
            assert json.load(response)["ok"]
    finally:
        server.worker.cancel()
        thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        serving.join()
    assert not thread.is_alive()


def test_preview_bounds_cache_invalidation_and_full_size_export(tmp_path, monkeypatch):
    Image.new("RGB", (2400, 1800), "#efdfcf").save(tmp_path / "source.png")
    text = Layer("text", "Text", "text", BoundingBox(20, 20, 500, 100), content="ONE",
                 style={"font_size": 60})
    project = ProjectDocument("abc", "test", "marketing", "marketing", {"width":2400, "height":1800}, {},
                              [Layer("bg", "BG", "raster", BoundingBox(0,0,2400,1800), asset="source.png"), text],
                              QualityReport("review", 80, {}))
    exporter = ProjectExporter()
    exporter.render_preview(project, tmp_path)
    with Image.open(tmp_path / "preview.png") as preview:
        assert preview.size == (1600, 1200)
    with Image.open(tmp_path / "source-preview.png") as original:
        assert original.size == (1600, 1200)
    original_render = exporter._save_figure
    calls = []
    def tracked(*args, **kwargs):
        calls.append(Path(args[2]).name)
        return original_render(*args, **kwargs)
    monkeypatch.setattr(exporter, "_save_figure", tracked)
    exporter.render_preview(project, tmp_path)
    assert calls == []
    text.content = "TWO"
    exporter.render_preview(project, tmp_path)
    assert calls == [".preview-next.png"]
    calls.clear()
    project.layers[0].opacity = .5
    exporter.render_preview(project, tmp_path)
    assert calls == [".preview-base-next.png", ".preview-next.png"]
    exporter.export_package(project, tmp_path, tmp_path / "output", ["png"])
    with Image.open(tmp_path / "output/preview.png") as exported:
        assert exported.size == (2400, 1800)


def test_render_failure_does_not_leak_figures(tmp_path, monkeypatch):
    project = ProjectDocument("abc", "test", "logo", "logo", {"width":100, "height":100}, {},
                              [Layer("bg", "BG", "vector", BoundingBox(0,0,100,100), asset="bad.svg")],
                              QualityReport("review", 80, {}))
    exporter = ProjectExporter()
    def fail(*args):
        raise ValueError("Broken vector")
    monkeypatch.setattr(exporter, "_draw_vector", fail)
    before = plt.get_fignums()
    for _ in range(3):
        with pytest.raises(ValueError):
            exporter._save_figure(project, tmp_path, tmp_path / "preview.png", "png", 100)
    assert plt.get_fignums() == before
