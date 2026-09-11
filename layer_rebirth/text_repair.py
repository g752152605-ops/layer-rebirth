"""Local, per-region text repair and bounded matching against installed fonts."""
from dataclasses import asdict
import hashlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from matplotlib.font_manager import FontProperties, findfont


@lru_cache(maxsize=1)
def font_candidates():
    result = []
    seen = set()
    for family in ("Arial", "Times New Roman", "Georgia", "Verdana", "Microsoft YaHei", "SimSun"):
        for weight, slant in (("normal", "normal"), ("bold", "normal"), ("normal", "italic"), ("bold", "italic")):
            try:
                path = findfont(FontProperties(family=family, weight=weight, style=slant), fallback_to_default=False)
                if path not in seen:
                    result.append((family, weight, slant, ImageFont.truetype(path, 64)))
                    seen.add(path)
            except (ValueError, OSError):
                continue
    return result


def match_font(layer, ink):
    content = str(layer.style.get("original_content", layer.content))
    if not content.strip() or len(content) > 80 or "\n" in content or not np.any(ink):
        return
    ys, xs = np.nonzero(ink)
    target = ink[ys.min():ys.max()+1, xs.min():xs.max()+1]
    target_small = cv2.resize(target, (192, 48), interpolation=cv2.INTER_AREA) / 255
    best = None
    for family, weight, slant, font in font_candidates():
        if any("\u4e00" <= c <= "\u9fff" for c in content) and family not in {"Microsoft YaHei", "SimSun"}:
            continue
        box = font.getbbox(content)
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= 0 or h <= 0 or w > 12000:
            continue
        canvas = Image.new("L", (w, h))
        ImageDraw.Draw(canvas).text((-box[0], -box[1]), content, font=font, fill=255)
        small = cv2.resize(np.asarray(canvas), (192, 48), interpolation=cv2.INTER_AREA) / 255
        score = float(np.mean(np.abs(target_small-small))) + .15 * abs(np.log((w/h)/(target.shape[1]/target.shape[0])))
        if best is None or score < best[0]:
            best = (score, family, weight, slant, 64 * target.shape[0] / h)
    if best:
        _, family, weight, slant, size = best
        layer.style.update(font_family=family, font_weight=weight, font_style=slant,
                           font_size=round(min(1000, max(1, size)), 1), ink_fit=True,
                           font_match="本机近似字体，非原字体恢复")


def _save_asset(project_dir, prefix, pixels):
    name = f"{prefix}-{hashlib.sha256(pixels.tobytes()).hexdigest()[:24]}.png"
    path = project_dir / name
    if not path.exists():
        temp = path.with_suffix(".tmp")
        Image.fromarray(pixels).save(temp, "PNG")
        temp.replace(path)
    return name


def prepare_text_repair(layer, rgba, regions, project_dir: Path, *, match=True):
    height, width = rgba.shape[:2]
    raw = layer.style.get("original_bbox", asdict(layer.bbox))
    layer.style["original_bbox"] = dict(raw)
    x0, y0 = max(0, int(raw["x"])), max(0, int(raw["y"]))
    x1, y1 = min(width, int(raw["x"]+raw["width"])), min(height, int(raw["y"]+raw["height"]))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("原字区域无效")
    pad = max(8, min(64, round((y1-y0)*.3)))
    left, top, right, bottom = max(0,x0-pad), max(0,y0-pad), min(width,x1+pad), min(height,y1+pad)
    crop = rgba[top:bottom,left:right]
    rgb = crop[:,:,:3]
    ring = np.ones(crop.shape[:2], bool)
    ring[y0-top:y1-top,x0-left:x1-left] = False
    samples = rgb[ring] if np.any(ring) else rgb.reshape(-1,3)
    background = np.median(samples,axis=0)
    delta = np.linalg.norm(rgb.astype(np.float32)-background,axis=2)
    spread = float(np.percentile(np.linalg.norm(samples-background,axis=1),65))
    threshold = max(5, min(36, spread*1.5))
    foreground = np.uint8(delta > threshold)*255
    lines = cv2.morphologyEx(foreground,cv2.MORPH_OPEN,np.ones((1,max(20,int(crop.shape[1]*.85))),np.uint8))
    lines |= cv2.morphologyEx(foreground,cv2.MORPH_OPEN,np.ones((max(20,int(crop.shape[0]*.9)),1),np.uint8))
    foreground[lines>0]=0
    count, labels, stats, centers = cv2.connectedComponentsWithStats(foreground,8)
    boxes=[]
    for other in regions:
        b=other.style.get("original_bbox",asdict(other.bbox))
        if b["x"]<right and b["y"]<bottom and b["x"]+b["width"]>left and b["y"]+b["height"]>top:
            boxes.append((other.id,b))
    owned=np.zeros(count,dtype=bool)
    ambiguous=False
    for index in range(1,count):
        cx,cy=centers[index]+[left,top]
        candidates=[]
        for identity,b in boxes:
            margin=max(2,min(6,b["height"]*.08))
            if not (b["x"]-margin<=cx<=b["x"]+b["width"]+margin and b["y"]-margin<=cy<=b["y"]+b["height"]+margin):
                continue
            score=abs(cy-(b["y"]+b["height"]/2))/max(1,b["height"])
            candidates.append((score,identity))
        candidates.sort()
        if candidates and candidates[0][1]==layer.id:
            owned[index]=True
            if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<.12:
                ambiguous=True
    ink=np.uint8(owned[labels])*255
    mask=cv2.dilate(ink,np.ones((3,3),np.uint8))
    # Protect actual neighbouring strokes (including their anti-aliased edges).
    neighbours=(foreground>0)&(ink==0)
    protection=cv2.dilate(np.uint8(neighbours)*255,np.ones((3,3),np.uint8)) | lines
    mask[protection>0]=0
    manual_protect=np.zeros_like(mask)
    strokes=layer.style.get("repair_strokes",[])
    if not isinstance(strokes,list) or len(strokes)>500:
        raise ValueError("修正笔画数量超限")
    for stroke in strokes:
        points=np.asarray(stroke.get("points",[]),dtype=float)
        radius=float(stroke.get("radius",5))
        if points.ndim!=2 or points.shape[1]!=2 or not 1<=len(points)<=2000 or not np.isfinite(points).all() or not 1<=radius<=64:
            raise ValueError("修正画笔参数无效")
        if stroke.get("mode") not in {"erase","protect"}:
            raise ValueError("修正画笔类型无效")
        dest=manual_protect if stroke["mode"]=="protect" else mask
        points=np.rint(points-[left,top]).astype(np.int32)
        points=np.clip(points,-100000,100000)
        for point in (points[0], points[-1]):
            cv2.circle(dest,tuple(point),round(radius),255,-1)
        if len(points)>1:
            cv2.polylines(dest,[points],False,255,max(1,round(radius*2)))
    mask[manual_protect>0]=0
    # A smooth gradient can have low overall spread but must not get a flat patch.
    edge_colors=[np.median(rgb[:3].reshape(-1,3),axis=0),np.median(rgb[-3:].reshape(-1,3),axis=0),
                 np.median(rgb[:,:3].reshape(-1,3),axis=0),np.median(rgb[:,-3:].reshape(-1,3),axis=0)]
    gradient=max(np.linalg.norm(a-b) for a in edge_colors for b in edge_colors)>8
    if spread<=12 and not gradient:
        repaired=np.empty_like(rgb);repaired[:]=np.clip(background,0,255).astype(np.uint8)
    else:
        repaired=cv2.inpaint(rgb,mask,3,cv2.INPAINT_TELEA)
    # The source glyph is independent of the destination and contains no rectangle.
    glyph_ink=(mask>0)&(delta>threshold)
    glyph=np.zeros_like(crop)
    if np.any(glyph_ink):
        strongest=rgb[glyph_ink][delta[glyph_ink]>=np.percentile(delta[glyph_ink],75)]
        color=np.median(strongest,axis=0)
        direction=background-color
        alpha=np.clip(np.sum((background-rgb.astype(float))*direction,axis=2)/max(1,float(direction@direction)),0,1)
        glyph[:,:,:3]=np.clip(color,0,255).astype(np.uint8)
        glyph[:,:,3]=np.uint8(np.rint(alpha*255))*np.uint8(glyph_ink)
    extent={"x":left,"y":top,"width":right-left,"height":bottom-top}
    layer.style.update(erase_mode="raster",repair_version=3,erase_bbox=extent,glyph_bbox=extent,
        erase_asset=_save_asset(project_dir,"erase",np.dstack((repaired,mask))),
        glyph_asset=_save_asset(project_dir,"glyph",glyph),
        mask_asset=_save_asset(project_dir,"mask",mask))
    layer.style.setdefault("appearance","editable" if layer.style.get("replacement_active") else "original")
    layer.style["repair_warning"]="笔画归属不确定，可用修正画笔检查。" if ambiguous else ("复杂背景或多色字形，请检查边缘。" if spread>12 or gradient else "")
    if match:
        match_font(layer,ink[y0-top:y1-top,x0-left:x1-left])
