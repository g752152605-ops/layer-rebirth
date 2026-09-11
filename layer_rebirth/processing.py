from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import vtracer
from PIL import Image, ImageOps

from .models import BoundingBox, Layer, ProjectDocument, QualityReport
from .storage import ProjectStorage
from .text_repair import prepare_text_repair


Image.MAX_IMAGE_PIXELS = 60_000_000
ALLOWED_INPUTS = {"PNG", "JPEG", "WEBP", "BMP"}


class ImageProcessor:
    def __init__(self, storage: ProjectStorage):
        self.storage = storage
        self._ocr = None

    def upgrade_text_repairs(self, project: ProjectDocument) -> bool:
        candidates = [layer for layer in project.layers if layer.kind == "text" and layer.style.get("candidate_only")]
        pending = [layer for layer in candidates if layer.style.get("repair_version") != 3]
        if not pending:
            return False
        directory = self.storage.project_dir(project.id)
        with Image.open(directory / "source.png") as source:
            image = source.convert("RGBA")
        # Old projects did not store exact original boxes. Recover them from OCR,
        # rather than treating a moved replacement box as the original text location.
        recognized = self._recognize(image) if any(not l.style.get("original_bbox") for l in pending) else []
        for layer in pending:
            if not layer.style.get("original_bbox"):
                matches = [r for r in recognized if r["text"].strip() == layer.style.get("original_content", layer.content)]
                if not matches:
                    raise ValueError("旧工程原字位置无法可靠恢复，请重新导入原图后编辑。")
                old = layer.style.get("erase_bbox", {"x":layer.bbox.x,"y":layer.bbox.y})
                match = min(matches, key=lambda r: (np.min(np.asarray(r["box"])[:,0])-old["x"])**2 + (np.min(np.asarray(r["box"])[:,1])-old["y"])**2)
                points = np.asarray(match["box"])
                x, y = points.min(axis=0)
                x1, y1 = points.max(axis=0)
                layer.style.update(original_polygon=points.tolist(), original_bbox={"x":float(x),"y":float(y),"width":float(x1-x),"height":float(y1-y)})
        for layer in pending:
            prepare_text_repair(layer, np.asarray(image), candidates, directory, match=not layer.style.get("replacement_active"))
        for layer in project.layers:
            if layer.id == "background" and layer.asset == "background-vector.svg":
                layer.kind, layer.asset, layer.name = "raster", "source.png", "保留原稿（位图）"
        return True

    def process(
        self,
        image_bytes: bytes,
        filename: str,
        requested_mode: str = "auto",
        settings: dict[str, Any] | None = None,
    ) -> ProjectDocument:
        settings = settings or {}
        project_id = str(uuid.uuid4())
        project_dir = self.storage.project_dir(project_id)
        image, source_format = self._load_image(image_bytes)
        width, height = image.size
        source_path = project_dir / "source.png"
        image.save(source_path, "PNG")

        analysis = self._analyze(image)
        looks_like_line_art = (
            float(analysis["grayscale_ratio"]) >= 0.96
            and float(analysis["edge_density"]) >= 0.08
            and int(analysis["color_count"]) <= 56
        )
        recommended = (
            "logo"
            if (analysis["color_count"] <= 28 and analysis["edge_density"] < 0.32) or looks_like_line_art
            else "marketing"
        )
        mode = recommended if requested_mode == "auto" else requested_mode
        if mode not in {"marketing", "logo", "format"}:
            raise ValueError("未知处理模式")

        if mode == "logo":
            layers, report = self._process_logo(source_path, image, analysis, settings)
        elif mode == "marketing":
            layers, report = self._process_marketing(source_path, image, analysis, settings)
        else:
            layers, report = self._process_format(image, analysis)

        project = ProjectDocument(
            id=project_id,
            name=Path(filename).stem[:80] or "未命名工程",
            mode=mode,
            recommended_mode=recommended,
            canvas={"width": width, "height": height, "unit": "px", "background": "transparent"},
            source={"name": Path(filename).name, "format": source_format, "asset": "source.png"},
            layers=layers,
            quality=report,
            export_settings={
                "formats": ["svg", "pdf", "eps", "png"],
                "transparent": True,
                "dpi": 300,
            },
        )
        self.storage.save(project)
        return project

    def _load_image(self, image_bytes: bytes) -> tuple[Image.Image, str]:
        if not image_bytes or len(image_bytes) > 50 * 1024 * 1024:
            raise ValueError("图片为空或超过 50 MB")
        from io import BytesIO

        stream = BytesIO(image_bytes)
        try:
            with Image.open(stream) as probe:
                probe.verify()
            stream.seek(0)
            with Image.open(stream) as opened:
                source_format = opened.format or ""
                if source_format not in ALLOWED_INPUTS:
                    raise ValueError("首版仅支持 PNG、JPG、WebP 和 BMP 图片")
                image = ImageOps.exif_transpose(opened).convert("RGBA")
        except Image.DecompressionBombError as exc:
            raise ValueError("图片像素尺寸过大") from exc
        except (OSError, SyntaxError) as exc:
            raise ValueError("图片损坏或格式不受支持") from exc
        if image.width < 8 or image.height < 8:
            raise ValueError("图片尺寸过小")
        return image, source_format

    @staticmethod
    def _analyze(image: Image.Image) -> dict[str, int | float]:
        sample = image.convert("RGB")
        sample.thumbnail((320, 320))
        array = np.asarray(sample)
        quantized = sample.quantize(colors=64)
        counts = quantized.getcolors(maxcolors=64) or []
        # Ignore antialiasing shades that occupy less than 0.5% of the sample.
        significant = max(2, round(sample.width * sample.height * 0.005))
        color_count = sum(count >= significant for count, _ in counts)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        edge_density = float(np.count_nonzero(cv2.Canny(gray, 80, 160))) / gray.size
        channel_spread = np.ptp(array.astype(np.int16), axis=2)
        grayscale_ratio = float(np.mean(channel_spread < 8))
        return {
            "color_count": color_count,
            "edge_density": round(edge_density, 4),
            "grayscale_ratio": round(grayscale_ratio, 4),
        }

    def _process_logo(
        self,
        source_path: Path,
        image: Image.Image,
        analysis: dict[str, int | float],
        settings: dict[str, Any],
    ) -> tuple[list[Layer], QualityReport]:
        vector_path = source_path.with_name("vector.svg")
        line_art = float(analysis["grayscale_ratio"]) >= 0.96 and float(analysis["edge_density"]) >= 0.08
        trace_settings = {**settings, "line_art": line_art}
        self._trace(source_path, vector_path, trace_settings)
        node_count = self._node_count(vector_path)
        warnings: list[str] = []
        if line_art:
            warnings.append("已按黑白线稿启用高精度描摹，并自动保留小字和细线。")
        if int(analysis["color_count"]) > 36 and not line_art:
            warnings.append("颜色较多，建议减少颜色后检查局部细节。")
        if node_count > 4000:
            warnings.append("节点数量较多，后续编辑可能不够顺畅。")
        score = 92
        if not line_art:
            score -= min(18, max(0, int(analysis["color_count"]) - 24) // 2)
        score -= 12 if node_count > 4000 else 0
        verdict = "ready" if score >= 85 else "review" if score >= 65 else "refine"
        layer = Layer(
            id="vector-artwork",
            name="矢量图形",
            kind="vector",
            bbox=BoundingBox(0, 0, image.width, image.height),
            asset="vector.svg",
        )
        report = QualityReport(
            verdict=verdict,
            score=max(0, score),
            metrics={
                "layer_count": 1,
                "text_count": 0,
                "color_count": int(analysis["color_count"]),
                "node_count": node_count,
                "ocr_confidence": "未启用",
            },
            warnings=warnings,
        )
        return [layer], report

    def _process_marketing(
        self,
        source_path: Path,
        image: Image.Image,
        analysis: dict[str, int | float],
        settings: dict[str, Any],
    ) -> tuple[list[Layer], QualityReport]:
        ocr_result = self._recognize(image)
        rgba = np.asarray(image)
        text_layers: list[Layer] = []
        confidences: list[float] = []

        for index, item in enumerate(ocr_result, start=1):
            points = np.asarray(item["box"], dtype=np.float32)
            x0 = max(0, int(np.floor(points[:, 0].min())))
            y0 = max(0, int(np.floor(points[:, 1].min())))
            x1 = min(image.width, int(np.ceil(points[:, 0].max())))
            y1 = min(image.height, int(np.ceil(points[:, 1].max())))
            if x1 <= x0 or y1 <= y0:
                continue
            confidence = float(item["score"])
            confidences.append(confidence)
            color = self._estimate_text_color(rgba, x0, y0, x1, y1)
            content = item["text"].strip()
            font_family = "Microsoft YaHei" if any("\u4e00" <= char <= "\u9fff" for char in content) else "Arial"
            layer = Layer(
                    id=f"text-{index}",
                    name=f"文字 {index}",
                    kind="text",
                    bbox=BoundingBox(x0, y0, x1 - x0, y1 - y0),
                    content=content,
                    style={
                        "font_family": font_family,
                        "font_size": max(8, round((y1 - y0) * 0.78, 1)),
                        "fill": color,
                        "text_anchor": "start",
                        "original_polygon": points.tolist(),
                        "original_bbox": {"x": x0, "y": y0, "width": x1-x0, "height": y1-y0},
                    },
                    confidence=round(confidence, 4),
                )
            text_layers.append(layer)

        raster_repairs = 0
        for layer in text_layers:
            layer.visible = False
            layer.name = layer.name.replace("文字", "候选文字")
            layer.style.update({
                "candidate_only": True,
                "replacement_active": False,
                "original_content": layer.content,
            })
            prepare_text_repair(layer, rgba, text_layers, source_path.parent)
            if layer.style.get("repair_warning"):
                raster_repairs += 1
        layers = [Layer(
            id="background", name="保留原稿（位图）", kind="raster",
            bbox=BoundingBox(0, 0, image.width, image.height), asset="source.png", locked=True,
        ), *text_layers]
        warnings = [
            "原稿保持不变，仅在修改文字时局部修补；照片背景仍为位图。",
            "文字按本机字体近似匹配，非原字体恢复；只修补原字笔画，重叠区域优先保护邻字。",
        ]
        if not text_layers:
            warnings.append("未识别到可编辑文字，请检查图片清晰度或保留原图。")
        low_confidence = sum(score < 0.88 for score in confidences)
        if low_confidence:
            warnings.append(f"有 {low_confidence} 处文字置信度偏低，修改前请核对。")
        if raster_repairs:
            warnings.append(f"有 {raster_repairs} 处背景复杂或文字框重叠，修改后请放大检查。")
        average = sum(confidences) / len(confidences) if confidences else 0
        return layers, QualityReport(
            verdict="review" if text_layers else "refine",
            score=max(0, round(76 + average * 18 - low_confidence * 8)) if text_layers else 56,
            metrics={"layer_count": len(layers), "text_count": len(text_layers),
                     "color_count": int(analysis["color_count"]), "node_count": 0,
                     "ocr_confidence": round(average * 100, 1)},
            warnings=warnings,
        )

    @staticmethod
    def _process_format(
        image: Image.Image, analysis: dict[str, int | float]
    ) -> tuple[list[Layer], QualityReport]:
        layer = Layer(
            id="source-raster",
            name="原始图片",
            kind="raster",
            bbox=BoundingBox(0, 0, image.width, image.height),
            asset="source.png",
        )
        report = QualityReport(
            verdict="ready",
            score=100,
            metrics={
                "layer_count": 1,
                "text_count": 0,
                "color_count": int(analysis["color_count"]),
                "node_count": 0,
                "ocr_confidence": "未启用",
            },
            warnings=["格式转换不会自动恢复文字或矢量图层。"],
        )
        return [layer], report

    def _recognize(self, image: Image.Image) -> list[dict[str, Any]]:
        if self._ocr is None:
            from rapidocr import RapidOCR

            self._ocr = RapidOCR(params={
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })
        rgb = np.asarray(image.convert("RGB"))
        output = self._ocr(rgb)
        boxes = output.boxes if output and output.boxes is not None else []
        texts = output.txts if output and output.txts is not None else []
        scores = output.scores if output and output.scores is not None else []
        return [
            {"box": box, "text": text, "score": score}
            for box, text, score in zip(boxes, texts, scores)
            if str(text).strip()
        ]

    @staticmethod
    def _estimate_text_color(
        rgba: np.ndarray, x0: int, y0: int, x1: int, y1: int
    ) -> str:
        crop = rgba[y0:y1, x0:x1, :3].astype(np.int16)
        if crop.size == 0:
            return "#161616"
        border = np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1]), axis=0)
        background = np.median(border, axis=0)
        distance = np.linalg.norm(crop - background, axis=2)
        foreground = crop[distance >= max(30, float(np.percentile(distance, 65)))]
        color = np.median(foreground, axis=0) if len(foreground) else np.array([22, 22, 22])
        red, green, blue = (int(max(0, min(255, value))) for value in color)
        return f"#{red:02x}{green:02x}{blue:02x}"

    @staticmethod
    def _trace(source_path: Path, vector_path: Path, settings: dict[str, Any]) -> None:
        line_art = bool(settings.get("line_art", False))
        color_precision = max(1, min(8, int(settings.get("color_precision", 6))))
        filter_speckle = 0 if line_art else max(0, min(64, int(settings.get("filter_speckle", 4))))
        curve_mode = settings.get("curve_mode", "spline")
        if curve_mode not in {"spline", "polygon", "pixel"}:
            curve_mode = "spline"
        vtracer.convert_image_to_svg_py(
            str(source_path),
            str(vector_path),
            colormode="binary" if line_art else "color",
            hierarchical="stacked",
            mode=curve_mode,
            filter_speckle=filter_speckle,
            color_precision=color_precision,
            layer_difference=8 if line_art else 16,
            corner_threshold=50 if line_art else 60,
            length_threshold=1.5 if line_art else 4.0,
            max_iterations=20 if line_art else 10,
            splice_threshold=45,
            path_precision=3 if line_art else 2,
        )

    @staticmethod
    def _node_count(vector_path: Path) -> int:
        svg = vector_path.read_text(encoding="utf-8")
        return len(re.findall(r"(?<![A-Za-z])[MLCQAZHVSTmlcqazhvst](?![A-Za-z])", svg))
