from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from .models import ProjectDocument


class ProjectStorage:
    def __init__(self, root: str | Path | None = None):
        if root is None:
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LayerRebirth",
                Path(os.environ.get("APPDATA", Path.home())) / "LayerRebirth",
                Path(tempfile.gettempdir()) / "LayerRebirth",
            ]
            root = self._first_writable_root(candidates)
        self.root = Path(root).resolve()
        self.projects = self.root / "projects"
        self.exports = self.root / "exports"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _first_writable_root(candidates: list[Path]) -> Path:
        last_error: OSError | None = None
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / f".write-test-{uuid.uuid4().hex}"
                probe.write_bytes(b"")
                probe.unlink()
                return candidate
            except OSError as exc:
                last_error = exc
        raise OSError("无法创建本地工程目录") from last_error

    def project_dir(self, project_id: str) -> Path:
        if not project_id or any(char not in "0123456789abcdef-" for char in project_id.lower()):
            raise ValueError("工程 ID 无效")
        path = (self.projects / project_id).resolve()
        if path.parent != self.projects:
            raise ValueError("工程路径无效")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def export_directory(self) -> str:
        try:
            value = json.loads((self.root / "export-preferences.json").read_text(encoding="utf-8"))
            directory = value.get("directory")
            if isinstance(directory, str) and Path(directory).is_absolute():
                return directory
        except (OSError, ValueError, AttributeError):
            pass
        return str(self.exports)

    def prepare_export_directory(self, value: object = None) -> Path:
        if value is None:
            return self.exports
        if not isinstance(value, str) or not value.strip() or not Path(value.strip()).is_absolute():
            raise ValueError("请选择有效的导出文件夹，或填写完整绝对路径")
        directory = Path(value.strip())
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
            return directory.resolve()
        except OSError as exc:
            raise ValueError("无法写入导出文件夹，请检查路径和写入权限") from exc

    def remember_export_directory(self, directory: Path) -> None:
        path = self.root / "export-preferences.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"directory": str(directory)}, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def save(self, project: ProjectDocument) -> Path:
        path = self.project_dir(project.id) / "project.layer.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        return path

    def load(self, project_id: str) -> ProjectDocument:
        path = self.project_dir(project_id) / "project.layer.json"
        if not path.exists():
            raise FileNotFoundError("工程不存在")
        return ProjectDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def recent(self, limit: int = 30) -> list[dict]:
        entries = []
        paths = sorted(self.projects.glob("*/project.layer.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths:
            if len(entries) >= limit:
                break
            try:
                project = self.load(path.parent.name)
                entries.append({"id": project.id, "name": project.name, "mode": project.mode,
                                "updated_at": path.stat().st_mtime})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(entries, key=lambda entry: entry["updated_at"], reverse=True)[:limit]

    def asset(self, project_id: str, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError("文件名无效")
        path = (self.project_dir(project_id) / name).resolve()
        if path.parent != self.project_dir(project_id):
            raise ValueError("文件路径无效")
        return path
