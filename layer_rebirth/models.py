from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
import math


LayerKind = Literal["text", "vector", "raster"]
Mode = Literal["marketing", "logo", "format"]


@dataclass
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass
class Layer:
    id: str
    name: str
    kind: LayerKind
    bbox: BoundingBox
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    content: str = ""
    asset: str = ""
    style: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None


@dataclass
class QualityReport:
    verdict: Literal["ready", "review", "refine"]
    score: int
    metrics: dict[str, int | float | str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProjectDocument:
    id: str
    name: str
    mode: Mode
    recommended_mode: Mode
    canvas: dict[str, int | float | str]
    source: dict[str, str]
    layers: list[Layer]
    quality: QualityReport
    export_settings: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProjectDocument":
        if value.get("schema_version", 1) != 1:
            raise ValueError("不支持的工程文件版本")
        canvas = value.get("canvas") or {}
        width = int(canvas.get("width", 0))
        height = int(canvas.get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("画布尺寸无效")
        raw_layers = value.get("layers") or []
        layers: list[Layer] = []
        for raw in raw_layers:
            kind = raw.get("kind")
            if kind not in {"text", "vector", "raster"}:
                raise ValueError("图层类型无效")
            bbox = BoundingBox(**raw.get("bbox", {}))
            style = dict(raw.get("style") or {})
            if kind == "text":
                for key, bounds in {"font_size": (1, 1000), "letter_spacing": (0, 100),
                                    "line_height": (.5, 4), "rotation": (-180, 180),
                                    "stroke_width": (0, 20)}.items():
                    if key in style:
                        number = float(style[key])
                        if not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
                            raise ValueError(f"文字参数 {key} 超出范围")
                        style[key] = number
                for key, allowed in {"font_weight": {"normal", "bold"}, "font_style": {"normal", "italic"},
                                     "text_fit": {"shrink", "actual"}, "text_align": {"left", "center", "right"},
                                     "background_mode": {"auto", "transparent", "solid"}}.items():
                    if key in style and style[key] not in allowed:
                        raise ValueError(f"文字参数 {key} 无效")
                if not all(math.isfinite(float(value)) for value in (bbox.x, bbox.y, bbox.width, bbox.height)) or not (0 < bbox.width <= 10000 and 0 < bbox.height <= 10000):
                    raise ValueError("文本框尺寸无效")
            opacity = max(0.0, min(1.0, float(raw.get("opacity", 1.0))))
            layers.append(
                Layer(
                    id=str(raw["id"]),
                    name=str(raw.get("name", "未命名图层")),
                    kind=kind,
                    bbox=bbox,
                    visible=bool(raw.get("visible", True)),
                    locked=bool(raw.get("locked", False)),
                    opacity=opacity,
                    content=str(raw.get("content", "")),
                    asset=str(raw.get("asset", "")),
                    style=style,
                    confidence=raw.get("confidence"),
                )
            )
        quality_raw = value.get("quality") or {}
        quality = QualityReport(
            verdict=quality_raw.get("verdict", "review"),
            score=max(0, min(100, int(quality_raw.get("score", 0)))),
            metrics=dict(quality_raw.get("metrics") or {}),
            warnings=[str(item) for item in quality_raw.get("warnings", [])],
        )
        mode = value.get("mode", "marketing")
        recommended = value.get("recommended_mode", mode)
        if mode not in {"marketing", "logo", "format"}:
            raise ValueError("处理模式无效")
        if recommended not in {"marketing", "logo", "format"}:
            recommended = mode
        return cls(
            id=str(value["id"]),
            name=str(value.get("name", "未命名工程")),
            mode=mode,
            recommended_mode=recommended,
            canvas={**canvas, "width": width, "height": height},
            source=dict(value.get("source") or {}),
            layers=layers,
            quality=quality,
            export_settings=dict(value.get("export_settings") or {}),
            schema_version=1,
            revision=max(0, int(value.get("revision", 0))),
        )
