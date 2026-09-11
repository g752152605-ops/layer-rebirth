from __future__ import annotations

import base64
import json
import mimetypes
import re
import threading
import uuid
import copy
import hashlib
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageOps

from .licensing import LicenseManager
from .models import ProjectDocument
from .storage import ProjectStorage
from .worker import ProcessingWorker, TaskBusy


MAX_REQUEST_BYTES = 70 * 1024 * 1024


class LayerRebirthService:
    def __init__(self, storage: ProjectStorage, licensing: LicenseManager):
        self.storage = storage
        self._processor = None
        self._exporter = None
        self.licensing = licensing
        self.lock = threading.Lock()

    @property
    def processor(self):
        # Heavy native libraries are needed only by the processing worker.
        if self._processor is None:
            from .processing import ImageProcessor
            self._processor = ImageProcessor(self.storage)
        return self._processor

    @property
    def exporter(self):
        if self._exporter is None:
            from .exporting import ProjectExporter
            self._exporter = ProjectExporter()
        return self._exporter

    @staticmethod
    def decode_file(payload: dict) -> tuple[bytes, str]:
        filename = Path(str(payload.get("name", "image.png"))).name
        data = str(payload.get("data", ""))
        if "," in data:
            data = data.split(",", 1)[1]
        try:
            content = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise ValueError("图片数据无效") from exc
        return content, filename

    def process(self, payload: dict) -> dict:
        content, filename = self.decode_file(payload.get("file") or {})
        with self.lock:
            project = self.processor.process(
                content,
                filename,
                requested_mode=str(payload.get("mode", "marketing")),
                settings=dict(payload.get("settings") or {}),
            )
            project_dir = self.storage.project_dir(project.id)
            self.exporter.render_preview(project, project_dir)
        return self._project_response(project)

    def update(self, project_id: str, payload: dict) -> dict:
        project = ProjectDocument.from_dict(payload.get("project") or {})
        if project.id != project_id:
            raise ValueError("工程 ID 不一致")
        project_dir = self.storage.project_dir(project.id)
        saved = self.storage.load(project_id)
        if payload.get("base_revision", saved.revision) != saved.revision:
            raise ValueError("REVISION_CONFLICT: 工程已更新，请重新打开后重试；当前输入仍保留。")
        for layer in project.layers:
            if layer.asset and (Path(layer.asset).name != layer.asset or not (project_dir / layer.asset).exists()):
                raise ValueError("工程资源无效")
            for key in ("erase_asset", "glyph_asset", "mask_asset"):
                asset = str(layer.style.get(key, ""))
                if asset and (Path(asset).name != asset or not (project_dir / asset).is_file()):
                    raise ValueError("文字修补资源无效")
        project.quality.metrics["layer_count"] = len(project.layers)
        project.quality.metrics["text_count"] = sum(layer.kind == "text" for layer in project.layers)
        with self.lock:
            self.processor.upgrade_text_repairs(project)
            previous = {l.id:l for l in saved.layers}
            changed_masks = [l for l in project.layers if l.kind == "text" and l.id in previous
                and l.style.get("repair_strokes", []) != previous[l.id].style.get("repair_strokes", [])]
            if changed_masks:
                from .text_repair import prepare_text_repair
                import numpy as np
                with Image.open(project_dir / "source.png") as source:
                    pixels = np.asarray(source.convert("RGBA"))
                for layer in changed_masks:
                    prepare_text_repair(layer,pixels,[l for l in project.layers if l.kind == "text"],project_dir,match=False)
            self.exporter.render_preview(project, project_dir)
            project.revision = saved.revision + 1
            self.storage.save(project)
        return self._project_response(project)

    def repair(self, project_id: str, layer_id: str, payload: dict) -> dict:
        from .text_repair import prepare_text_repair
        project = self.storage.load(project_id)
        if payload.get("base_revision") != project.revision:
            raise ValueError("REVISION_CONFLICT: 工程已更新，请重试修补。")
        layer = next((l for l in project.layers if l.id == layer_id and l.kind == "text"), None)
        if layer is None:
            raise ValueError("文字图层不存在")
        layer.style["repair_strokes"] = payload.get("strokes", [])
        with Image.open(self.storage.project_dir(project_id) / "source.png") as source:
            import numpy as np
            prepare_text_repair(layer, np.asarray(source.convert("RGBA")),
                [l for l in project.layers if l.kind == "text"], self.storage.project_dir(project_id), match=False)
        layer.style["replacement_active"] = True
        layer.visible = True
        return self.update(project_id, {"project": project.to_dict(), "base_revision": project.revision})

    def drag_assets(self, project_id: str, layer_id: str, payload: dict) -> dict:
        project = self.storage.load(project_id)
        if payload.get("base_revision") != project.revision:
            raise ValueError("REVISION_CONFLICT: 工程已更新，请重新准备预览。")
        layer = next((l for l in project.layers if l.id == layer_id and l.kind == "text"), None)
        if layer is None:
            raise ValueError("文字图层不存在")
        directory = self.storage.project_dir(project_id)
        layer.visible = True
        layer.style["replacement_active"] = True
        key = hashlib.sha256(json.dumps(project.to_dict(), sort_keys=True).encode()).hexdigest()[:24]
        base_name, object_name = f"drag-base-{key}.png", f"drag-object-{key}.png"
        if not (directory / base_name).exists():
            base = copy.deepcopy(project)
            next(l for l in base.layers if l.id == layer_id).style["hide_content"] = True
            self.exporter._save_figure(base,directory,directory/base_name,"png",100,max_size=1600)
        if not (directory / object_name).exists():
            object_layer = copy.deepcopy(layer)
            object_layer.style["background_mode"] = "transparent"
            overlay = replace(project,layers=[object_layer],canvas={**project.canvas,"background":"transparent"},export_settings={"transparent":True})
            self.exporter._save_figure(overlay,directory,directory/object_name,"png",100,max_size=1600)
        prefix = f"/api/projects/{project_id}/files/"
        return {"revision":project.revision,"base_url":prefix+base_name,"object_url":prefix+object_name,
            "source_url":prefix+"source.png","mask_url":prefix+layer.style["mask_asset"],"repair_bbox":layer.style["erase_bbox"]}

    def reopen(self, project_id: str) -> dict:
        with self.lock:
            project = self.storage.load(project_id)
            if self.processor.upgrade_text_repairs(project):
                self.storage.save(project)
            self.exporter.render_preview(project, self.storage.project_dir(project_id))
        return self._project_response(project)

    def export(self, project_id: str, payload: dict) -> dict:
        project = self.storage.load(project_id)
        if self.processor.upgrade_text_repairs(project):
            self.storage.save(project)
        formats = payload.get("formats") or ["svg", "pdf", "eps", "png", "report", "project"]
        export_id = str(uuid.uuid4())[:8]
        export_root = self.storage.prepare_export_directory(payload.get("destination"))
        destination = export_root / f"{self._safe_name(project.name)}-{export_id}"
        outputs = self.exporter.export_package(
            project,
            self.storage.project_dir(project.id),
            destination,
            formats,
            watermark=False,
        )
        self.storage.remember_export_directory(export_root)
        return {
            "destination": str(destination),
            "files": [{"name": path.name, "path": str(path)} for path in outputs],
            "trial_limited": False,
        }

    def batch(self, payload: dict) -> dict:
        files = payload.get("files") or []
        if not files or len(files) > 30:
            raise ValueError("批量处理支持 1–30 张图片")
        output_format = str(payload.get("output_format", "png")).lower()
        if output_format not in {"png", "jpg", "webp", "pdf"}:
            raise ValueError("批量输出格式无效")
        width = self._dimension(payload.get("width", 0))
        height = self._dimension(payload.get("height", 0))
        dpi = max(72, min(600, int(payload.get("dpi", 144))))
        background = str(payload.get("background", "transparent"))
        if background not in {"transparent", "white", "black"}:
            raise ValueError("批量背景设置无效")
        export_root = self.storage.prepare_export_directory(payload.get("destination"))
        results = []
        for item in files:
            content, filename = self.decode_file(item)
            content = self._prepare_batch_image(content, width, height, background)
            project = self.processor.process(content, filename, requested_mode="format")
            project.export_settings.update({"dpi": dpi, "transparent": background == "transparent"})
            project.canvas["background"] = background
            self.storage.save(project)
            destination = export_root / f"batch-{project.id[:8]}"
            outputs = self.exporter.export_package(
                project,
                self.storage.project_dir(project.id),
                destination,
                [output_format],
            )
            results.append({"source": filename, "files": [str(path) for path in outputs]})
        self.storage.remember_export_directory(export_root)
        return {"count": len(results), "results": results}

    @staticmethod
    def _dimension(value: object) -> int:
        dimension = int(value or 0)
        if dimension < 0 or dimension > 10_000:
            raise ValueError("批量尺寸需在 0–10000 像素之间")
        return dimension

    @staticmethod
    def _prepare_batch_image(content: bytes, width: int, height: int, background: str) -> bytes:
        with Image.open(BytesIO(content)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA")
        if width == 0 and height == 0:
            target = image
        else:
            if width == 0:
                width = max(1, round(image.width * height / image.height))
            if height == 0:
                height = max(1, round(image.height * width / image.width))
            if width * height > 60_000_000:
                raise ValueError("批量目标尺寸像素过大")
            fitted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
            colors = {"transparent": (0, 0, 0, 0), "white": (255, 255, 255, 255), "black": (0, 0, 0, 255)}
            target = Image.new("RGBA", (width, height), colors[background])
            target.alpha_composite(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        output = BytesIO()
        target.save(output, "PNG")
        return output.getvalue()

    def _project_response(self, project: ProjectDocument) -> dict:
        stamp = uuid.uuid4().hex[:8]
        return {
            "project": project.to_dict(),
            "original_url": f"/api/projects/{project.id}/files/source-preview.png",
            "preview_url": f"/api/projects/{project.id}/files/preview.png?v={stamp}",
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "-", value, flags=re.UNICODE).strip("-.")
        return cleaned[:60] or "project"


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, web_root: Path, storage: ProjectStorage, licensing: LicenseManager):
        super().__init__(address, RequestHandler)
        self.web_root = web_root.resolve()
        self.service = LayerRebirthService(storage, licensing)
        self.worker = ProcessingWorker(storage.root, licensing.public_key_path)

    def server_close(self):
        self.worker.close()
        super().server_close()


class RequestHandler(BaseHTTPRequestHandler):
    server: AppServer

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/export-directory":
            self._json({"ok": True, "directory": self.server.service.storage.export_directory()})
            return
        if parsed.path == "/api/fonts":
            from matplotlib.font_manager import fontManager
            self._json({"ok": True, "fonts": sorted({font.name for font in fontManager.ttflist})})
            return
        if parsed.path == "/api/health":
            self._json({"ok": True, "offline": True, "version": "0.1.0", "license": self.server.service.licensing.status()})
            return
        if parsed.path == "/api/projects":
            self._json({"ok": True, "projects": self.server.service.storage.recent()})
            return
        project_match = re.fullmatch(r"/api/projects/([0-9a-f-]+)", parsed.path)
        if project_match:
            try:
                self._json({"ok": True, **self.server.worker.run("reopen", project_match.group(1))})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._json({"ok": False, "error": f"无法打开工程：{exc}"}, HTTPStatus.BAD_REQUEST)
            return
        match = re.fullmatch(r"/api/projects/([0-9a-f-]+)/files/([^/]+)", parsed.path)
        if match:
            try:
                path = self.server.service.storage.asset(match.group(1), unquote(match.group(2)))
                self._file(path)
            except (ValueError, FileNotFoundError):
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        relative = "index.html" if parsed.path == "/" else unquote(parsed.path.lstrip("/"))
        target = (self.server.web_root / relative).resolve()
        if self.server.web_root not in target.parents and target != self.server.web_root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._file(target)

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            path = urlparse(self.path).path
            if path == "/api/cancel":
                self.server.worker.cancel()
                result = {}
            elif path == "/api/process":
                result = self.server.worker.run("process", payload)
            elif path == "/api/batch":
                result = self.server.worker.run("batch", payload)
            else:
                update_match = re.fullmatch(r"/api/projects/([0-9a-f-]+)", path)
                export_match = re.fullmatch(r"/api/projects/([0-9a-f-]+)/export", path)
                text_match = re.fullmatch(r"/api/projects/([0-9a-f-]+)/text/([^/]+)/(repair|drag-assets)", path)
                if text_match:
                    operation = "repair" if text_match.group(3) == "repair" else "drag_assets"
                    result = self.server.worker.run(operation,text_match.group(1),unquote(text_match.group(2)),payload)
                elif export_match:
                    result = self.server.worker.run("export", export_match.group(1), payload)
                elif update_match:
                    result = self.server.worker.run("update", update_match.group(1), payload)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
            self._json({"ok": True, **result})
        except TaskBusy as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
        except TimeoutError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.REQUEST_TIMEOUT)
        except (ValueError, FileNotFoundError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT if str(exc).startswith("REVISION_CONFLICT:") else HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"ok": False, "error": f"处理失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("请求为空或超过 70 MB")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求数据无效") from exc
        if not isinstance(value, dict):
            raise ValueError("请求格式无效")
        return value

    def _json(self, value: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store" if path.name == "preview.png" else "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


def create_server(
    host: str,
    port: int,
    web_root: Path,
    data_root: Path | None = None,
    public_key_path: Path | None = None,
) -> AppServer:
    storage = ProjectStorage(data_root)
    key_path = public_key_path or web_root.parent / "license_public_key.pem"
    return AppServer((host, port), web_root, storage, LicenseManager(key_path, storage.root))
