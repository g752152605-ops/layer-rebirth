"""Local, reversible masked inpainting. Source pixels outside the mask stay intact."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .text_repair import _save_asset


def cleanup_asset(directory: Path, strokes: list) -> str:
    if not isinstance(strokes, list) or len(strokes) > 500:
        raise ValueError("选区最多保留 500 笔")
    if not strokes:
        return "source.png"
    with Image.open(directory / "source.png") as source:
        pixels = np.asarray(source.convert("RGBA")).copy()
    height, width = pixels.shape[:2]
    checked = []
    for stroke in strokes:
        if not isinstance(stroke, dict) or stroke.get("mode") not in {"erase", "protect"}:
            raise ValueError("画笔类型无效")
        radius = float(stroke.get("radius", 10))
        points = stroke.get("points")
        shape = stroke.get("shape", "brush")
        if (not math.isfinite(radius) or not 1 <= radius <= 128 or shape not in {"brush", "rect"}
                or not isinstance(points, list) or not 1 <= len(points) <= 2000
                or (shape == "rect" and len(points) != 2)):
            raise ValueError("选区参数无效")
        coordinates = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("选区坐标无效")
            x, y = map(float, point)
            if not all(math.isfinite(v) for v in (x, y)) or not (0 <= x < width and 0 <= y < height):
                raise ValueError("选区超出图片范围")
            coordinates.append((round(x), round(y)))
        checked.append((stroke["mode"], shape, max(1, round(radius)), coordinates))
    erase = [s for s in checked if s[0] == "erase"]
    if not erase:
        return "source.png"
    # Work only around the chosen area, keeping large images out of inpaint buffers.
    x0 = max(0, min(x - r for _, _, r, pts in erase for x, y in pts) - 32)
    y0 = max(0, min(y - r for _, _, r, pts in erase for x, y in pts) - 32)
    x1 = min(width, max(x + r for _, _, r, pts in erase for x, y in pts) + 33)
    y1 = min(height, max(y + r for _, _, r, pts in erase for x, y in pts) + 33)
    if (x1 - x0) * (y1 - y0) > 12_000_000:
        raise ValueError("选区跨度过大，请分成较小区域处理")
    mask = np.zeros((y1-y0, x1-x0), np.uint8)
    protected = np.zeros_like(mask)
    for mode, shape, radius, points in checked:
        target = protected if mode == "protect" else mask
        local = np.array([(x-x0, y-y0) for x,y in points], dtype=np.int32)
        if shape == "rect":
            a, b = local
            cv2.rectangle(target, tuple(np.minimum(a,b)), tuple(np.maximum(a,b)), 255, -1)
        else:
            cv2.polylines(target, [local], False, 255, radius * 2)
            for point in (local[0],local[-1]):
                cv2.circle(target, tuple(point), radius, 255, -1)
    mask[protected > 0] = 0
    if not np.any(mask):
        return "source.png"
    if not np.any(mask == 0):
        raise ValueError("请保留选区周围背景，不能清除整张图片")
    crop = pixels[y0:y1,x0:x1]
    ring = (cv2.dilate(mask, np.ones((7,7),np.uint8)) > 0) & (mask == 0) & (protected == 0)
    samples = crop[:,:,:3][ring].astype(np.float32)
    color = np.median(samples,axis=0) if len(samples) else None
    flat = color is not None and len(samples) >= 50 and np.percentile(np.abs(samples-color),99) <= 3
    if flat:
        crop[mask > 0,:3] = np.round(color).astype(np.uint8)
    else:
        repaired = cv2.inpaint(np.ascontiguousarray(crop[:,:,:3][:,:,::-1]), mask, 3, cv2.INPAINT_TELEA)
        crop[mask > 0,:3] = repaired[:,:,::-1][mask > 0]
    # The original alpha and every unmasked pixel are preserved exactly.
    return _save_asset(directory, "cleanup", pixels)
