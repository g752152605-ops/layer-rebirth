from __future__ import annotations

import argparse
import base64
import os
import sys
import threading
import multiprocessing
import time
import traceback
import webbrowser
from pathlib import Path

from layer_rebirth.server import create_server


IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_IMAGE_BYTES = 50 * 1024 * 1024


def encode_image_paths(paths: list[str] | tuple[str, ...]) -> list[dict[str, object]]:
    """Read images selected by the native dialog for the local webview bridge."""
    images: list[dict[str, object]] = []
    for raw_path in paths:
        path = Path(raw_path)
        mime_type = IMAGE_TYPES.get(path.suffix.lower())
        if not mime_type or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        images.append({
            "name": path.name,
            "type": mime_type,
            "size": path.stat().st_size,
            "data": f"data:{mime_type};base64,{encoded}",
        })
    return images


class NativeApi:
    def __init__(self) -> None:
        self._window = None

    def choose_export_directory(self, initial_path: str = "") -> str | None:
        import webview

        if self._window is None:
            return None
        directory = initial_path if Path(initial_path).is_dir() else ""
        paths = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory=directory, allow_multiple=False,
        )
        return str(paths[0]) if paths else None

    def choose_images(self) -> list[dict[str, object]]:
        import webview

        if self._window is None:
            return []
        paths = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.webp;*.bmp)",),
        )
        return encode_image_paths(paths or ())


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def main() -> None:
    parser = argparse.ArgumentParser(description="图层重生 - Windows 本地图片可编辑化工具")
    parser.add_argument("--browser", action="store_true", help="使用系统浏览器打开开发界面")
    parser.add_argument("--serve-only", action="store_true", help="仅启动本地服务，用于自动化验证")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    server = create_server(
        "127.0.0.1", args.port, resource_path("web"), args.data_dir,
        public_key_path=resource_path("license_public_key.pem"),
    )
    def startup_log(message: str) -> None:
        try:
            with (server.service.storage.root / "startup.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} +{time.monotonic()-started:.2f}s {message}\n")
        except OSError:
            pass

    startup_log("local server ready")
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    if args.browser or args.serve_only:
        if args.browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import webview

        native_api = NativeApi()
        window = webview.create_window(
            "图层重生",
            url,
            width=1440,
            height=900,
            min_size=(1080, 700),
            background_color="#F3EDE2",
            js_api=native_api,
        )
        native_api._window = window
        window.events.loaded += lambda: startup_log("page and native bridge ready")
        webview.start(gui="edgechromium", debug=args.debug, private_mode=True)
    except Exception:
        startup_log("desktop startup failed: " + traceback.format_exc())
        webbrowser.open(url)
        thread.join()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
