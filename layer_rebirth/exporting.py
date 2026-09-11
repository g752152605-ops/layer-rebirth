from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, replace
import html
import os
import re
import shutil
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Iterable

os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("MPLCONFIGDIR", str(Path(os.environ.get("LOCALAPPDATA", ".")) / "LayerRebirth" / "mpl-cache"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path
from matplotlib.font_manager import FontProperties
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.textpath import TextPath, TextToPath
from PIL import Image

from .models import BoundingBox, Layer, ProjectDocument


SVG_NS = "http://www.w3.org/2000/svg"
VERDICT_LABELS = {"ready": "基础检查通过", "review": "建议检查", "refine": "建议人工精修"}


class ProjectExporter:
    PREVIEW_SIZE = 1600

    @staticmethod
    def composition_layers(project):
        """Apply all source repairs behind all replacement objects."""
        patches, layers = [], []
        for layer in project.layers:
            if layer.kind != "text" or layer.style.get("repair_version", 0) < 3:
                layers.append(layer)
                continue
            if not layer.visible or not layer.style.get("replacement_active"):
                continue
            if layer.style.get("background_mode", "auto") == "auto":
                patches.append(Layer("patch-"+layer.id, "原字修补", "raster",
                    BoundingBox(**layer.style["erase_bbox"]), asset=layer.style["erase_asset"]))
            if layer.style.get("hide_content"):
                continue
            if layer.style.get("appearance") == "original" and layer.content == layer.style.get("original_content", layer.content) and layer.style.get("glyph_asset"):
                raw, box = layer.style["original_bbox"], layer.style["glyph_bbox"]
                sx, sy = layer.bbox.width/raw["width"], layer.bbox.height/raw["height"]
                glyph_box = BoundingBox(layer.bbox.x+(box["x"]-raw["x"])*sx,
                    layer.bbox.y+(box["y"]-raw["y"])*sy,box["width"]*sx,box["height"]*sy)
                layers.append(Layer(layer.id,layer.name,"raster",glyph_box,opacity=layer.opacity,asset=layer.style["glyph_asset"]))
            else:
                layers.append(replace(layer, style={**layer.style,
                    "replacement_active": layer.style.get("background_mode") == "solid"}))
        first = next((i for i,l in enumerate(layers) if l.id in {t.id for t in project.layers if t.kind=="text"}),len(layers))
        return layers[:first]+patches+layers[first:]

    @staticmethod
    def _preview_key(project: ProjectDocument, project_dir: Path) -> str:
        assets = []
        for layer in project.layers:
            for asset in (layer.asset, layer.style.get("erase_asset", ""), layer.style.get("glyph_asset", "")):
                if asset:
                    stat = (project_dir / asset).stat()
                    assets.append((asset, stat.st_size, stat.st_mtime_ns))
        payload = {"version": 1, "canvas": project.canvas, "layers": [asdict(layer) for layer in project.layers],
                   "transparent": project.export_settings.get("transparent", True), "assets": assets}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def render_preview(self, project: ProjectDocument, project_dir: Path) -> Path:
        target = project_dir / "preview.png"
        marker = project_dir / ".preview-key"
        key = self._preview_key(project, project_dir)
        original = project_dir / "source.png"
        original_preview = project_dir / "source-preview.png"
        if original.exists() and (not original_preview.exists() or original.stat().st_mtime_ns > original_preview.stat().st_mtime_ns):
            with Image.open(original) as source:
                source.thumbnail((self.PREVIEW_SIZE, self.PREVIEW_SIZE), Image.Resampling.LANCZOS)
                source.save(project_dir / ".source-preview-next.png", "PNG")
            (project_dir / ".source-preview-next.png").replace(original_preview)
        if target.exists() and marker.exists() and marker.read_text() == key:
            return target
        prefix = []
        for layer in project.layers:
            if layer.kind == "text":
                break
            prefix.append(layer)
        preview_project = project
        if prefix and len(prefix) < len(project.layers):
            base = replace(project, layers=prefix)
            base_key = self._preview_key(base, project_dir)
            base_marker = project_dir / ".preview-base-key"
            base_path = project_dir / ".preview-base.png"
            if not base_path.exists() or not base_marker.exists() or base_marker.read_text() != base_key:
                staging = project_dir / ".preview-base-next.png"
                self._save_figure(base, project_dir, staging, "png", 100, max_size=self.PREVIEW_SIZE)
                staging.replace(base_path)
                base_marker.write_text(base_key)
            preview_project = replace(project, layers=[Layer(
                "preview-base", "预览底图", "raster",
                BoundingBox(0, 0, project.canvas["width"], project.canvas["height"]), asset=base_path.name,
            ), *project.layers[len(prefix):]])
        staging = project_dir / ".preview-next.png"
        self._save_figure(preview_project, project_dir, staging, "png", 100, max_size=self.PREVIEW_SIZE)
        staging.replace(target)
        marker.write_text(key)
        return target

    def export_package(
        self,
        project: ProjectDocument,
        project_dir: Path,
        destination: Path,
        formats: Iterable[str],
        watermark: bool = False,
    ) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        requested = {item.lower() for item in formats}
        allowed = {"svg", "pdf", "eps", "png", "jpg", "webp", "report", "project"}
        if not requested or not requested <= allowed:
            raise ValueError("导出格式无效")
        outputs: list[Path] = []

        if "svg" in requested:
            editable = destination / "editable.svg"
            editable.write_text(self._build_svg(project, project_dir, outlined=False), encoding="utf-8")
            outputs.append(editable)
            outlined = destination / "outlined.svg"
            outlined.write_text(self._build_svg(project, project_dir, outlined=True), encoding="utf-8")
            outputs.append(outlined)
        if "pdf" in requested:
            path = destination / "print.pdf"
            self._save_figure(project, project_dir, path, "pdf", dpi=self._export_dpi(project, 300))
            outputs.append(path)
        if "eps" in requested:
            path = destination / "legacy.eps"
            self._save_figure(project, project_dir, path, "eps", dpi=300)
            outputs.append(path)
        for suffix in ("png", "jpg", "webp"):
            if suffix not in requested:
                continue
            path = destination / f"preview.{suffix}"
            self._save_figure(project, project_dir, path, "jpeg" if suffix == "jpg" else suffix, dpi=self._export_dpi(project, 144))
            if watermark:
                self._watermark(path)
            outputs.append(path)
        if "report" in requested:
            path = destination / "quality-report.html"
            path.write_text(self._quality_html(project), encoding="utf-8")
            outputs.append(path)
        if "project" in requested:
            path = destination / f"{project.name}.layer.json"
            source = project_dir / "project.layer.json"
            shutil.copy2(source, path)
            outputs.append(path)
        return outputs

    @staticmethod
    def _export_dpi(project: ProjectDocument, fallback: int) -> int:
        return max(72, min(600, int(project.export_settings.get("dpi", fallback))))

    def _build_svg(self, project: ProjectDocument, project_dir: Path, outlined: bool) -> str:
        width = int(project.canvas["width"])
        height = int(project.canvas["height"])
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f"  <title>{html.escape(project.name)}</title>",
            "  <metadata>Generated locally by Layer Rebirth. No source image was uploaded.</metadata>",
        ]
        for layer in self.composition_layers(project):
            if not layer.visible:
                continue
            layer_id = self._safe_id(layer.id)
            parts.append(f'  <g id="{layer_id}" opacity="{1.0 if layer.kind == "text" else layer.opacity:.3f}">')
            if layer.kind == "vector":
                parts.extend(
                    self._safe_vector_paths(
                        project_dir / layer.asset,
                        "    ",
                        self._safe_color(layer.style.get("fill_override"))
                        if layer.style.get("fill_override")
                        else None,
                    )
                )
            elif layer.kind == "raster":
                asset = project_dir / layer.asset
                mime = "image/png" if asset.suffix.lower() == ".png" else "image/jpeg"
                encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
                bbox = layer.bbox
                parts.append(
                    f'    <image x="{bbox.x}" y="{bbox.y}" width="{bbox.width}" height="{bbox.height}" '
                    f'href="data:{mime};base64,{encoded}" preserveAspectRatio="none"/>'
                )
            elif layer.kind == "text":
                parts.extend(self._svg_text_erase(layer, project_dir, "    "))
                fill = self._safe_color(layer.style.get("fill"))
                stroke = self._safe_color(layer.style.get("stroke_color", "#000000"))
                stroke_width = float(layer.style.get("stroke_width", 0))
                paint = f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" stroke-linejoin="round" paint-order="stroke fill"'
                if outlined:
                    parts.append(f'    <path d="{self._text_path(layer)}" {paint} opacity="{layer.opacity:.3f}"/>')
                else:
                    size, chunks, decorations = self._text_layout(layer)
                    family = html.escape(self._svg_font_family(layer), quote=True)
                    weight = "bold" if layer.style.get("font_weight") == "bold" else "normal"
                    italic = "italic" if layer.style.get("font_style") == "italic" else "normal"
                    box = layer.bbox
                    angle = float(layer.style.get("rotation", 0))
                    parts.append(f'    <g opacity="{layer.opacity:.3f}" transform="rotate({angle} {box.x + box.width / 2} {box.y + box.height / 2})">')
                    for text, x, y in chunks:
                        parts.append(f'      <text x="{x:.4f}" y="{y:.4f}" font-family="{family}" '
                                     f'font-size="{size:.4f}px" font-weight="{weight}" font-style="{italic}" '
                                     f'{paint} xml:space="preserve">{html.escape(text)}</text>')
                    for path in decorations:
                        parts.append(f'      <path d="{self._mpl_path_to_svg(path.codes, path.vertices)}" {paint}/>')
                    parts.append('    </g>')
            parts.append("  </g>")
        parts.append("</svg>")
        return "\n".join(parts)

    def _svg_text_erase(self, layer: Layer, project_dir: Path, indent: str) -> list[str]:
        if not layer.style.get("replacement_active") or layer.style.get("background_mode") == "transparent":
            return []
        bbox = self._erase_bbox(layer)
        if not bbox:
            return []
        x, y, width, height = bbox
        if layer.style.get("background_mode") == "solid" or layer.style.get("erase_mode") == "solid":
            color = self._safe_color(layer.style.get("background_color", "#ffffff") if layer.style.get("background_mode") == "solid" else layer.style.get("erase_color"))
            return [f'{indent}<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" fill="{color}"/>']
        asset_name = str(layer.style.get("erase_asset", ""))
        if layer.style.get("erase_mode") != "raster" or Path(asset_name).name != asset_name:
            return []
        asset = project_dir / asset_name
        if not asset.is_file():
            return []
        encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
        return [
            f'{indent}<image x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" '
            f'href="data:image/png;base64,{encoded}" preserveAspectRatio="none"/>'
        ]

    @staticmethod
    def _erase_bbox(layer: Layer) -> tuple[float, float, float, float] | None:
        raw = layer.style.get("erase_bbox")
        if not isinstance(raw, dict):
            return None
        try:
            x = float(raw["x"])
            y = float(raw["y"])
            width = float(raw["width"])
            height = float(raw["height"])
        except (KeyError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return x, y, width, height

    @staticmethod
    def _safe_vector_paths(path: Path, indent: str, fill_override: str | None = None) -> list[str]:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        safe: list[str] = []
        allowed = {"d", "fill", "fill-opacity", "stroke", "stroke-width", "opacity", "transform", "fill-rule"}
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "path" or not element.get("d"):
                continue
            attrs = []
            for key, value in element.attrib.items():
                if key in allowed:
                    if key == "fill" and fill_override and not ProjectExporter._is_near_white(value):
                        value = fill_override
                    attrs.append(f'{key}="{html.escape(value, quote=True)}"')
            safe.append(f"{indent}<path {' '.join(attrs)}/>")
        return safe

    @staticmethod
    def _safe_color(value: object) -> str:
        text = str(value or "#161616")
        return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else "#161616"

    @staticmethod
    def _is_near_white(value: str) -> bool:
        match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
        if not match:
            return value.strip().lower() in {"white", "none", "transparent"}
        red, green, blue = (int(match.group(1)[index : index + 2], 16) for index in (0, 2, 4))
        return min(red, green, blue) >= 245

    @staticmethod
    def _safe_id(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", value)
        return cleaned or "layer"

    @staticmethod
    def _font(layer: Layer) -> FontProperties:
        family = str(layer.style.get("font_family", "Arial"))
        if re.search(r"[\u3400-\u9fff]", layer.content) and family in {"Arial", "Times New Roman"}:
            family = "Microsoft YaHei"
        return FontProperties(family=family, weight=layer.style.get("font_weight", "normal"),
                              style=layer.style.get("font_style", "normal"))

    @staticmethod
    def _svg_font_family(layer: Layer) -> str:
        family = str(layer.style.get("font_family", "Arial")).replace('"', "")
        if re.search(r"[\u3400-\u9fff]", layer.content):
            return f"{family}, Microsoft YaHei, sans-serif"
        return family

    def _text_path(self, layer: Layer) -> str:
        path = self._fitted_text_path(layer)
        return self._mpl_path_to_svg(path.codes, path.vertices)

    def _text_layout(self, layer: Layer):
        """Shared line/glyph positions for live SVG and outlined/raster exports."""
        box = layer.bbox
        size = float(layer.style.get("font_size", max(8, box.height * .78)))
        gap = float(layer.style.get("letter_spacing", 0))
        leading = float(layer.style.get("line_height", 1.2))
        font = self._font(layer)
        font.set_size(size)
        if (layer.style.get("ink_fit") and layer.style.get("text_fit", "shrink") == "shrink"
                and "\n" not in layer.content and layer.content.strip() and not gap
                and not layer.style.get("underline") and not layer.style.get("strikethrough")):
            bounds = TextPath((0, 0), layer.content, size=size, prop=font).get_extents()
            scale = min(1., box.width / max(.001, bounds.width), box.height / max(.001, bounds.height))
            align = {"left": 0, "center": .5, "right": 1}.get(layer.style.get("text_align", "left"), 0)
            x = box.x - bounds.x0 * scale + (box.width - bounds.width * scale) * align
            y = box.y + bounds.y1 * scale
            return size * scale, [(layer.content, x, y)], []
        measure = TextToPath()
        lines = layer.content.replace("\r", "").split("\n")
        raw_lines = []
        for line in lines:
            chunks = []
            if gap and line:
                x = 0.0
                for char in line:
                    chunks.append((char, x))
                    x += measure.get_text_width_height_descent(char, font, False)[0] + gap
                width = max(0, x - gap)
            else:
                chunks = [(line, 0.0)] if line else []
                width = measure.get_text_width_height_descent(line, font, False)[0] if line else 0
            # Include italic overhangs when fitting inside the box.
            for text, x in chunks:
                if text.strip():
                    path = TextPath((0, 0), text, size=size, prop=font)
                    width = max(width, x + float(path.vertices[:, 0].max()))
            raw_lines.append((chunks, width))
        height = size * (1 + max(0, len(lines) - 1) * leading)
        width = max((line[1] for line in raw_lines), default=0)
        scale = min(1.0, box.width / max(.001, width), box.height / max(.001, height)) if layer.style.get("text_fit", "shrink") == "shrink" else 1.0
        chunks, decorations = [], []
        for index, (line_chunks, line_width) in enumerate(raw_lines):
            align = layer.style.get("text_align", "left")
            offset = (box.width - line_width * scale) * ({"left": 0, "center": .5, "right": 1}.get(align, 0))
            x0 = box.x + offset
            baseline = box.y + (size * .82 + index * size * leading) * scale
            for text, x in line_chunks:
                chunks.append((text, x0 + x * scale, baseline))
            for key, y in [("underline", baseline + size * .1 * scale), ("strikethrough", baseline - size * .3 * scale)]:
                if layer.style.get(key) and line_width:
                    transform = matplotlib.transforms.Affine2D().scale(line_width * scale, max(.5, size * .055 * scale)).translate(x0, y)
                    decorations.append(MplPath.unit_rectangle().transformed(transform))
        return size * scale, chunks, decorations

    def _fitted_font_size(self, layer: Layer) -> float:
        return self._text_layout(layer)[0]

    def _fitted_text_path(self, layer: Layer) -> MplPath:
        size, chunks, paths = self._text_layout(layer)
        for text, x, y in chunks:
            if text.strip():
                path = TextPath((0, 0), text, size=size, prop=self._font(layer), usetex=False)
                paths.append(path.transformed(matplotlib.transforms.Affine2D().scale(1, -1).translate(x, y)))
        result = MplPath.make_compound_path(*paths) if paths else MplPath([(layer.bbox.x, layer.bbox.y)], [MplPath.MOVETO])
        box = layer.bbox
        angle = float(layer.style.get("rotation", 0))
        return result.transformed(matplotlib.transforms.Affine2D().rotate_deg_around(box.x + box.width / 2, box.y + box.height / 2, angle))

    @staticmethod
    def _mpl_path_to_svg(codes: np.ndarray, vertices: np.ndarray) -> str:
        commands: list[str] = []
        index = 0
        while index < len(codes):
            code = codes[index]
            x, y = vertices[index]
            if code == MplPath.MOVETO:
                commands.append(f"M{x:.2f},{y:.2f}")
            elif code == MplPath.LINETO:
                commands.append(f"L{x:.2f},{y:.2f}")
            elif code == MplPath.CURVE3 and index + 1 < len(codes):
                x2, y2 = vertices[index + 1]
                commands.append(f"Q{x:.2f},{y:.2f} {x2:.2f},{y2:.2f}")
                index += 1
            elif code == MplPath.CURVE4 and index + 2 < len(codes):
                x2, y2 = vertices[index + 1]
                x3, y3 = vertices[index + 2]
                commands.append(f"C{x:.2f},{y:.2f} {x2:.2f},{y2:.2f} {x3:.2f},{y3:.2f}")
                index += 2
            elif code == MplPath.CLOSEPOLY:
                commands.append("Z")
            index += 1
        return " ".join(commands)

    def _save_figure(
        self,
        project: ProjectDocument,
        project_dir: Path,
        target: Path,
        format_name: str,
        dpi: int,
        max_size: int | None = None,
    ) -> None:
        width = int(project.canvas["width"])
        height = int(project.canvas["height"])
        preview_scale = min(1.0, max_size / max(width, height)) if max_size else 1.0
        output_width, output_height = max(1, round(width * preview_scale)), max(1, round(height * preview_scale))
        raster_output = format_name in {"png", "jpeg", "webp"}
        render_dpi = 100 if raster_output else dpi
        background = str(project.canvas.get("background", "transparent"))
        transparent = (project.export_settings.get("transparent", True)
                       and background == "transparent" and format_name in {"png", "webp"})
        facecolor = "none" if transparent else "black" if background == "black" else "white"
        fig = plt.figure(figsize=(output_width / render_dpi, output_height / render_dpi), dpi=render_dpi, facecolor=facecolor)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        try:
            for order, layer in enumerate(self.composition_layers(project)):
                if not layer.visible:
                    continue
                zorder = order * 3
                if layer.kind == "vector":
                    self._draw_vector(
                        ax,
                        project_dir / layer.asset,
                        layer.opacity,
                        self._safe_color(layer.style.get("fill_override"))
                        if layer.style.get("fill_override")
                        else None,
                        zorder,
                    )
                elif layer.kind == "raster":
                    bbox = layer.bbox
                    with Image.open(project_dir / layer.asset) as source:
                        if max_size:
                            source.thumbnail((max(1, round(bbox.width * preview_scale)), max(1, round(bbox.height * preview_scale))), Image.Resampling.LANCZOS)
                        image = source.convert("RGBA")
                    ax.imshow(
                        image,
                        extent=(bbox.x, bbox.x + bbox.width, bbox.y + bbox.height, bbox.y),
                        alpha=layer.opacity,
                        interpolation="none",
                        zorder=zorder,
                    )
                else:
                    self._draw_text_erase(ax, layer, project_dir, zorder)
                    ax.add_patch(
                        PathPatch(
                            self._fitted_text_path(layer),
                            transform=ax.transData,
                            facecolor=self._safe_color(layer.style.get("fill")),
                            edgecolor=self._safe_color(layer.style.get("stroke_color", "#000000")),
                            linewidth=float(layer.style.get("stroke_width", 0)) * 72 / render_dpi * preview_scale,
                            joinstyle="round",
                            alpha=layer.opacity,
                            zorder=zorder + 1,
                        )
                    )
            metadata = {"Title": project.name, "Creator": "Layer Rebirth 0.1.0"}
            kwargs = {"format": format_name, "facecolor": facecolor, "edgecolor": "none"}
            if format_name in {"pdf", "svg"}:
                kwargs["metadata"] = metadata
            if format_name == "png" and dpi == render_dpi:
                # Preview already uses the requested DPI; avoid decoding and
                # compressing the same PNG a second time through Pillow.
                fig.savefig(target, dpi=render_dpi, format="png", facecolor=facecolor,
                            edgecolor="none", transparent=transparent)
            elif raster_output:
                buffer = BytesIO()
                fig.savefig(buffer, dpi=render_dpi, format="png", facecolor=facecolor,
                            edgecolor="none", transparent=transparent)
                buffer.seek(0)
                with Image.open(buffer) as rendered:
                    output = rendered.convert("RGB" if format_name == "jpeg" else "RGBA")
                    options = {"lossless": True} if format_name == "webp" else {"dpi": (dpi, dpi)}
                    output.save(target, format=format_name, **options)
            else:
                fig.savefig(target, dpi=dpi, **kwargs)
        finally:
            plt.close(fig)

    def _draw_text_erase(self, ax, layer: Layer, project_dir: Path, zorder: int) -> None:
        if not layer.style.get("replacement_active") or layer.style.get("background_mode") == "transparent":
            return
        bbox = self._erase_bbox(layer)
        if not bbox:
            return
        x, y, width, height = bbox
        if layer.style.get("background_mode") == "solid" or layer.style.get("erase_mode") == "solid":
            ax.add_patch(
                Rectangle(
                    (x, y), width, height,
                    facecolor=self._safe_color(layer.style.get("background_color", "#ffffff") if layer.style.get("background_mode") == "solid" else layer.style.get("erase_color")),
                    edgecolor="none",
                    zorder=zorder,
                )
            )
            return
        asset_name = str(layer.style.get("erase_asset", ""))
        if layer.style.get("erase_mode") != "raster" or Path(asset_name).name != asset_name:
            return
        asset = project_dir / asset_name
        if asset.is_file():
            image = Image.open(asset).convert("RGBA")
            ax.imshow(image, extent=(x, x + width, y + height, y), zorder=zorder)

    def _draw_vector(
        self,
        ax,
        svg_path: Path,
        opacity: float,
        fill_override: str | None = None,
        zorder: int = 0,
    ) -> None:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "path" or not element.get("d"):
                continue
            path = self._parse_svg_path(element.get("d", ""))
            path = path.transformed(self._svg_transform(element.get("transform", "")))
            fill = element.get("fill", "#000000")
            if fill == "none":
                fill = "none"
            elif fill_override and not self._is_near_white(fill):
                fill = fill_override
            ax.add_patch(
                PathPatch(
                    path,
                    facecolor=fill,
                    edgecolor=element.get("stroke", "none"),
                    linewidth=float(element.get("stroke-width", 0) or 0),
                    alpha=opacity * float(element.get("opacity", 1) or 1),
                    zorder=zorder,
                )
            )

    @staticmethod
    def _parse_svg_path(value: str) -> MplPath:
        pen = RecordingPen()
        parse_path(value, pen)
        vertices: list[tuple[float, float]] = []
        codes: list[int] = []
        for command, points in pen.value:
            if command == "moveTo":
                vertices.append(points[0])
                codes.append(MplPath.MOVETO)
            elif command == "lineTo":
                vertices.append(points[0])
                codes.append(MplPath.LINETO)
            elif command == "curveTo":
                for point in points:
                    vertices.append(point)
                    codes.append(MplPath.CURVE4)
            elif command == "qCurveTo":
                for point in points:
                    if point is not None:
                        vertices.append(point)
                        codes.append(MplPath.CURVE3)
            elif command in {"closePath", "endPath"} and vertices:
                vertices.append(vertices[-1])
                codes.append(MplPath.CLOSEPOLY)
        return MplPath(vertices, codes)

    @staticmethod
    def _svg_transform(value: str) -> matplotlib.transforms.Affine2D:
        transform = matplotlib.transforms.Affine2D()
        for name, raw in re.findall(r"(translate|scale|matrix)\s*\(([^)]*)\)", value):
            numbers = [float(item) for item in re.split(r"[\s,]+", raw.strip()) if item]
            if name == "translate" and numbers:
                transform.translate(numbers[0], numbers[1] if len(numbers) > 1 else 0)
            elif name == "scale" and numbers:
                transform.scale(numbers[0], numbers[1] if len(numbers) > 1 else numbers[0])
            elif name == "matrix" and len(numbers) == 6:
                a, b, c, d, e, f = numbers
                transform = transform + matplotlib.transforms.Affine2D.from_values(a, b, c, d, e, f)
        return transform

    @staticmethod
    def _watermark(path: Path) -> None:
        with Image.open(path) as source:
            dpi = source.info.get("dpi", (300, 300))
            image = source.convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(overlay)
        text = "图层重生 · 体验版"
        font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", max(16, image.width // 45))
        box = draw.textbbox((0, 0), text, font=font)
        x = max(12, image.width - (box[2] - box[0]) - 24)
        y = max(12, image.height - (box[3] - box[1]) - 22)
        draw.rounded_rectangle((x - 10, y - 8, image.width - 12, image.height - 12), radius=8, fill=(22, 22, 22, 168))
        draw.text((x, y), text, fill=(255, 255, 255, 235), font=font)
        Image.alpha_composite(image, overlay).convert("RGB" if path.suffix.lower() == ".jpg" else "RGBA").save(path, dpi=dpi)

    @staticmethod
    def _quality_html(project: ProjectDocument) -> str:
        metrics = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in project.quality.metrics.items()
        )
        warnings = "".join(f"<li>{html.escape(item)}</li>" for item in project.quality.warnings) or "<li>未发现明显风险</li>"
        label = VERDICT_LABELS[project.quality.verdict]
        return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>{html.escape(project.name)} - 交付体检</title>
<style>body{{font:16px/1.7 'Microsoft YaHei',sans-serif;max-width:760px;margin:48px auto;color:#211f1b;background:#f6f1e7}}main{{background:#fff;padding:40px;border-top:6px solid #d35332}}h1{{margin-top:0}}.score{{font:700 42px Georgia;color:#d35332}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #ddd}}th{{width:42%}}</style>
<main><p>图层重生 · 本地交付体检</p><h1>{html.escape(project.name)}</h1>
<p class="score">{project.quality.score}/100</p><h2>{label}</h2><table>{metrics}</table>
<h2>风险提示</h2><ul>{warnings}</ul><p>评分来自原图分析与基础规则，不代表视觉还原度或印前合格认证。请核对替代字体、文字可读性和修补区域。</p><p>本报告由本地处理生成，图片未上传网络。</p></main></html>"""


# Imported late in type annotations above to keep the vector helpers compact.
import numpy as np
