// A lightweight draft renderer. The saved preview/export remains authoritative.
const livePreview = {
  token: 0,
  clear() {
    this.token++;
    $("#livePreview").hidden = true;
  },
  settled() {
    const token = ++this.token, url = state.current?.preview_url;
    if (!url) return this.clear();
    workbench.loadImage(url).then(() => {
      if (token === this.token) $("#livePreview").hidden = true;
    }).catch(() => {});
  },
  async update() {
    const token = ++this.token, layer = currentLayer();
    if (!layer || layer.kind !== "text" || layer.style.appearance === "original"
        || (layer.style.background_mode || "auto") !== "auto") {
      $("#livePreview").hidden = true;
      return;
    }
    const id = layer.id, projectId = state.current.project.id;
    try {
      const assets = await workbench.prepareAssets();
      if (!assets || token !== this.token || currentLayer()?.id !== id
          || state.current?.project.id !== projectId) return;
      const canvas = $("#livePreview"), project = state.current.project;
      const ratio = Math.min(1, 1600 / Math.max(project.canvas.width, project.canvas.height));
      canvas.width = Math.round(project.canvas.width * ratio);
      canvas.height = Math.round(project.canvas.height * ratio);
      const context = canvas.getContext("2d");
      context.drawImage(assets.baseImage, 0, 0, canvas.width, canvas.height);
      context.scale(ratio, ratio);
      this.drawText(context, currentLayer());
      setView("result");
      canvas.hidden = false;
    } catch (_) {
      // A missing draft asset must not interrupt editing or automatic saving.
      if (token === this.token) $("#livePreview").hidden = true;
    }
  },
  drawText(ctx, layer) {
    const s = layer.style, b = layer.bbox;
    const size = Number(s.font_size) || Math.max(8, b.height * .78);
    const gap = Number(s.letter_spacing) || 0, leading = Number(s.line_height) || 1.2;
    const family = JSON.stringify(s.font_family || "Arial");
    ctx.save();
    ctx.font = `${s.font_style || "normal"} ${s.font_weight || "normal"} ${size}px ${family}`;
    const lines = String(layer.content).replace(/\r/g, "").split("\n");
    const widths = lines.map(line => gap
      ? Array.from(line).reduce((sum, char) => sum + ctx.measureText(char).width, 0) + Math.max(0, Array.from(line).length - 1) * gap
      : ctx.measureText(line).width);
    const ink = s.ink_fit && lines.length === 1 && lines[0].trim() && !gap && !s.underline && !s.strikethrough;
    const metrics = ctx.measureText(lines[0]);
    const width = ink ? metrics.actualBoundingBoxLeft + metrics.actualBoundingBoxRight : Math.max(...widths);
    const height = ink ? metrics.actualBoundingBoxAscent + metrics.actualBoundingBoxDescent : size * (1 + (lines.length - 1) * leading);
    const scale = (s.text_fit || "shrink") === "shrink" ? Math.min(1, b.width / Math.max(.001, width), b.height / Math.max(.001, height)) : 1;
    const align = {left:0, center:.5, right:1}[s.text_align || "left"] || 0;
    ctx.globalAlpha = layer.opacity ?? 1;
    ctx.fillStyle = s.fill || "#161616";
    ctx.strokeStyle = s.stroke_color || "#000000";
    ctx.lineWidth = (Number(s.stroke_width) || 0) / scale;
    ctx.translate(b.x + b.width / 2, b.y + b.height / 2);
    ctx.rotate((Number(s.rotation) || 0) * Math.PI / 180);
    ctx.translate(-b.width / 2, -b.height / 2);
    ctx.scale(scale, scale);
    lines.forEach((line, index) => {
      let x = (b.width / scale - (ink ? width : widths[index])) * align + (ink ? metrics.actualBoundingBoxLeft : 0);
      const y = ink ? metrics.actualBoundingBoxAscent : size * .82 + index * size * leading;
      const start = x;
      for (const part of gap ? Array.from(line) : [line]) {
        if (s.stroke_width > 0) ctx.strokeText(part, x, y);
        ctx.fillText(part, x, y);
        x += ctx.measureText(part).width + gap;
      }
      if (s.underline) ctx.fillRect(start, y + size * .1, widths[index], Math.max(.5 / scale, size * .055));
      if (s.strikethrough) ctx.fillRect(start, y - size * .3, widths[index], Math.max(.5 / scale, size * .055));
    });
    ctx.restore();
  },
};
