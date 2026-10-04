"""One persistent, killable process for OCR and rendering; the UI server stays responsive."""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import time
from pathlib import Path


class TaskBusy(ValueError):
    pass


def _serve(connection, root: str, public_key: str):
    # Imports and native computation live in this child, not the WebView process.
    import cv2
    from .server import LayerRebirthService
    from .storage import ProjectStorage
    from .licensing import LicenseManager

    cv2.setNumThreads(2)
    service = LayerRebirthService(ProjectStorage(root), LicenseManager(Path(public_key), Path(root)))
    try:
        while True:
            operation, request_path, result_path = connection.recv()
            try:
                arguments = json.loads(Path(request_path).read_text(encoding="utf-8"))
                result = getattr(service, operation)(*arguments)
                Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                connection.send((True, ""))
            except Exception as exc:
                connection.send((False, str(exc)))
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class ProcessingWorker:
    def __init__(self, root: Path, public_key: Path, timeout: float = 180):
        self.root, self.public_key, self.timeout = root, public_key, timeout
        self._directory = tempfile.TemporaryDirectory(prefix="worker-", dir=root)
        self._gate = threading.Lock()
        self._cancel = threading.Event()
        self._closed = False
        self._process = None
        self._connection = None
        self.operation = None

    def _start(self):
        if self._process is not None and self._process.is_alive():
            return
        self._stop()
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_serve, args=(child, str(self.root), str(self.public_key)), daemon=True)
        self._process.start()
        child.close()

    def _stop(self):
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=3)
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def cancel(self):
        self._cancel.set()

    def run(self, operation: str, *arguments):
        if self._closed:
            raise ValueError("处理服务已关闭")
        if operation not in {"process", "update", "reopen", "export", "batch", "repair", "drag_assets", "cleanup"}:
            raise ValueError("未知后台任务")
        if not self._gate.acquire(blocking=False):
            raise TaskBusy("已有任务正在处理，请等待或先取消当前任务。")
        self._cancel.clear()
        self.operation = operation
        started = time.monotonic()
        outcome = "error"
        request = Path(self._directory.name) / "request.json"
        result = Path(self._directory.name) / "result.json"
        try:
            self._start()
            # Keep large image payloads out of a blocking Pipe.send.
            request.write_text(json.dumps(arguments, ensure_ascii=False), encoding="utf-8")
            self._connection.send((operation, str(request), str(result)))
            while True:
                if self._connection.poll(.1):
                    success, message = self._connection.recv()
                    if not success:
                        raise ValueError(message)
                    value = json.loads(result.read_text(encoding="utf-8"))
                    outcome = "ok"
                    return value
                if self._cancel.is_set():
                    outcome = "cancelled"
                    self._stop()
                    raise ValueError("任务已取消，后台处理已停止。可从最近工程确认最后保存状态。")
                if time.monotonic() - started > self.timeout:
                    outcome = "timeout"
                    self._stop()
                    raise TimeoutError("任务处理超时，后台进程已重置。请缩小图片或分批处理后重试。")
        except TimeoutError:
            raise
        except (EOFError, BrokenPipeError, OSError) as exc:
            self._stop()
            raise ValueError("后台处理进程异常退出，已重置；可重新打开工程后重试。") from exc
        finally:
            try:
                for path in (request, result):
                    path.unlink(missing_ok=True)
                log = self.root / "performance.jsonl"
                if log.exists() and log.stat().st_size > 1024 * 1024:
                    log.replace(log.with_suffix(".previous.jsonl"))
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"time": time.time(), "operation": operation,
                                             "seconds": round(time.monotonic() - started, 3), "outcome": outcome}) + "\n")
            except OSError:
                pass
            finally:
                self.operation = None
                self._gate.release()

    def close(self):
        self._closed = True
        self.cancel()
        with self._gate:
            self._stop()
            self._directory.cleanup()
