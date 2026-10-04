const state = {
  files: [],
  current: null,
  selectedLayerId: null,
  view: "result",
  zoom: 1,
  undo: [], redo: [], committed: null, busy: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const els = {
  fileInput: $("#fileInput"), chooseFileText: $("#chooseFileText"), dropzone: $("#dropzone"), process: $("#processButton"),
  export: $("#exportButton"), newProject: $("#newProjectButton"), busy: $("#busyOverlay"),
  busyText: $("#busyText"), toast: $("#toast"), recommendation: $("#recommendation"),
  layerList: $("#layerList"), emptyLayerList: $("#emptyLayerList"), layerCount: $("#layerCount"),
  emptyStage: $("#emptyStage"), compareStage: $("#compareStage"), compareControl: $("#compareControl"),
  originalImage: $("#originalImage"), resultImage: $("#resultImage"), imageBoard: $("#imageBoard"),
  textOverlayLayer: $("#textOverlayLayer"), canvasEditHint: $("#canvasEditHint"),
  compareRange: $("#compareRange"), comparisonLine: $("#comparisonLine"),
  qualityScore: $("#qualityScore"), qualityVerdict: $("#qualityVerdict"),
  metricGrid: $("#metricGrid"), warningList: $("#warningList"), selectedLayerTitle: $("#selectedLayerTitle"),
  emptyEditor: $("#emptyEditor"), layerEditor: $("#layerEditor"), textContent: $("#textContent"),
  restoreOriginal: $("#restoreOriginalButton"),
  fontFamily: $("#fontFamily"), layerColor: $("#layerColor"), layerOpacity: $("#layerOpacity"),
  textContentLabel: $("#textContentLabel"), fontFamilyLabel: $("#fontFamilyLabel"), layerColorLabel: $("#layerColorLabel"),
  traceSettings: $("#traceSettings"), batchSettings: $("#batchSettings"), batchFormat: $("#batchFormat"),
  batchWidth: $("#batchWidth"), batchHeight: $("#batchHeight"), batchDpi: $("#batchDpi"), batchBackground: $("#batchBackground"),
};

const textFields = [
  ["fontSize", "font_size", 28, "number"], ["textFit", "text_fit", "shrink"],
  ["fontWeight", "font_weight", "normal"], ["fontStyle", "font_style", "normal"],
  ["textUnderline", "underline", false, "checkbox"], ["textStrike", "strikethrough", false, "checkbox"],
  ["letterSpacing", "letter_spacing", 0, "number"], ["lineHeight", "line_height", 1.2, "number"],
  ["textAlign", "text_align", "left"], ["textRotation", "rotation", 0, "number"],
  ["strokeWidth", "stroke_width", 0, "number"], ["strokeColor", "stroke_color", "#000000"],
  ["textBackground", "background_mode", "auto"], ["textBackgroundColor", "background_color", "#ffffff"],
];

function updateTextBackgroundHint() {
  const mode = $("#textBackground").value;
  $("#textBackgroundColorLabel").hidden = mode !== "solid";
  $("#textBackgroundHint").textContent = mode === "transparent"
    ? "透明模式不覆盖底图，也不会清除原字；如有重叠，可移动新文字或改用自动修补。"
    : mode === "solid" ? "以自选底色覆盖原字区域。" : "默认只清除原字笔画，新文字背景透明。" + (currentLayer()?.style.repair_warning || "") + (currentLayer()?.style.font_match ? " 字体为本机近似匹配，可在下方调整。" : "");
}

async function loadFonts() {
  try {
    const result = await apiGet("/api/fonts");
    const selected = els.fontFamily.value;
    result.fonts.forEach(name => {
      if (!Array.from(els.fontFamily.options).some(option => option.value === name)) {
        const option = document.createElement("option");
        option.value = name; option.textContent = name; els.fontFamily.append(option);
      }
    });
    els.fontFamily.value = selected;
  } catch (error) {
    showToast("本机字体列表暂不可用，仍可使用默认字体。", true);
  }
}

function selectedMode() {
  return $("input[name='mode']:checked").value;
}

function setFiles(files) {
  const allowed = ["image/png", "image/jpeg", "image/webp", "image/bmp"];
  const valid = Array.from(files).filter((file) => allowed.includes(file.type) && file.size <= 50 * 1024 * 1024);
  if (!valid.length) {
    showToast("请选择 50 MB 以内的 PNG、JPG、WebP 或 BMP 图片。", true);
    return;
  }
  const selected = valid.slice(0, selectedMode() === "format" ? 30 : 1);
  if (selected.reduce((total, file) => total + file.size, 0) > 50 * 1024 * 1024) {
    showToast("本次图片总大小超过 50 MB，请分批处理。", true);
    return;
  }
  state.files = selected;
  els.process.disabled = false;
  const countText = state.files.length === 1 ? state.files[0].name : `已选择 ${state.files.length} 张图片`;
  els.chooseFileText.textContent = countText;
  if (typeof workbench !== "undefined") workbench.previewFiles();
  if (selectedMode() !== "format" && valid.length > 1) {
    showToast("此功能一次处理一张图片，已载入第 1 张；批量转格式请先选择“格式与尺寸转换”。");
  }
}

function readFile(file) {
  if (typeof file.data === "string") {
    return Promise.resolve({ name: file.name, data: file.data });
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: reader.result });
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function request(path, options, timeout = 195000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal });
    const result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.error || "本地服务响应异常");
    return result;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("等待本地服务响应超时。请重新打开工程确认最后保存状态后再试。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

let apiQueue = Promise.resolve();
async function api(path, body) {
  const run = () => request(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }, path === "/api/cancel" ? 10000 : 195000);
  if (path === "/api/cancel") return run();
  const task = apiQueue.then(run, run);
  apiQueue = task.catch(() => {});
  return task;
}

async function apiGet(path) {
  return request(path, { cache: "no-store" }, /^\/api\/projects\/[0-9a-f-]+$/.test(path) ? 195000 : 15000);
}

async function initializeApp() {
  try {
    await apiGet("/api/health");
    await loadFonts();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function processFiles() {
  if (state.busy || !state.files.length) return;
  const destination = selectedMode() === "format"
    ? await chooseExportDirectory() : undefined;
  if (destination === null) return;
  if (state.current && !await saveProject()) return;
  setBusy(true, state.files.length > 1 ? "正在批量转换…" : "正在本地分析图片…");
  els.process.disabled = true;
  try {
    if (selectedMode() === "format") {
      const files = await Promise.all(state.files.map(readFile));
      const result = await api("/api/batch", {
        files,
        destination,
        output_format: els.batchFormat.value,
        width: Number(els.batchWidth.value),
        height: Number(els.batchHeight.value),
        dpi: Number(els.batchDpi.value),
        background: els.batchBackground.value,
      });
      const locations = result.results.map((item) => item.files.join("、")).join("\n");
      showToast(`已完成 ${result.count} 张图片\n${locations}`);
      return;
    }
    const file = await readFile(state.files[0]);
    const result = await api("/api/process", {
      file,
      mode: selectedMode(),
      settings: {
        color_precision: Number($("#colorPrecision").value),
        filter_speckle: Number($("#speckle").value),
      },
    });
    state.current = result;
    resetHistory();
    state.selectedLayerId = result.project.layers.find((layer) => layer.kind === "text")?.id || result.project.layers[0]?.id;
    renderProject();
    showToast("本地处理完成，可以检查图层并导出。", false);
    if (result.project.mode === "cleanup") { setBusy(false); await cleanupTool.open(); }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
    els.process.disabled = false;
  }
}

function renderProject({ preserveEditor = false } = {}) {
  if (!state.current) return;
  const { project, original_url: originalUrl, preview_url: previewUrl } = state.current;
  els.emptyStage.hidden = true;
  els.compareStage.hidden = false;
  els.compareControl.hidden = state.view !== "compare";
  els.originalImage.src = originalUrl;
  els.resultImage.src = previewUrl;
  els.imageBoard.style.aspectRatio = `${project.canvas.width} / ${project.canvas.height}`;
  const maxWidth = project.canvas.width;
  els.imageBoard.style.width = `${maxWidth}px`;
  els.imageBoard.style.height = "auto";
  els.imageBoard.style.zoom = state.zoom;
  els.export.disabled = false;
  renderLayers();
  renderCanvasTextTargets();
  renderQuality();
  if (!preserveEditor) renderEditor();
  setView(state.view);
  if (typeof workbench !== "undefined") workbench.render();
  if (typeof cleanupTool !== "undefined") cleanupTool.renderAction();
}

function renderCanvasTextTargets() {
  const focusId = document.activeElement?.dataset?.layerId;
  els.textOverlayLayer.replaceChildren();
  const project = state.current?.project;
  if (!project) {
    els.canvasEditHint.hidden = true;
    return;
  }
  const textLayers = project.layers.filter((layer) => layer.kind === "text");
  els.canvasEditHint.hidden = textLayers.length === 0;
  textLayers.forEach((layer) => {
    const target = document.createElement("button");
    target.type = "button";
    const inactiveCandidate = layer.style.candidate_only && !layer.style.replacement_active;
    const classes = ["canvas-text-target"];
    if (inactiveCandidate) classes.push("candidate-inactive");
    if (layer.style.replacement_active) classes.push("replaced");
    if (layer.confidence != null && layer.confidence < .88) classes.push("low-confidence");
    if (layer.id === state.selectedLayerId) classes.push("selected");
    target.className = classes.join(" ");
    target.dataset.layerId = layer.id;
    const status = inactiveCandidate ? "原稿文字" : layer.style.replacement_active ? "已替换" : "可编辑文字";
    target.ariaLabel = `修改${status}：${layer.content}`;
    target.title = `${status} · 点击修改“${layer.content}”`;
    target.style.left = `${layer.bbox.x / project.canvas.width * 100}%`;
    target.style.top = `${layer.bbox.y / project.canvas.height * 100}%`;
    target.style.width = `${layer.bbox.width / project.canvas.width * 100}%`;
    target.style.height = `${layer.bbox.height / project.canvas.height * 100}%`;
    if (typeof workbench !== "undefined") workbench.bindText(target, layer);
    else target.addEventListener("click", () => beginCanvasTextEdit(layer.id));
    els.textOverlayLayer.append(target);
  });
  if (focusId) Array.from(els.textOverlayLayer.children).find(node => node.dataset.layerId === focusId)?.focus();
}

function beginCanvasTextEdit(id) {
  if (state.busy) return;
  const project = state.current?.project;
  if (!project) return;
  state.selectedLayerId = id;
  renderLayers();
  renderEditor();
  renderCanvasTextTargets();
  const layer = currentLayer();
  const target = Array.from(els.textOverlayLayer.children).find((node) => node.dataset.layerId === id);
  if (!layer || !target) return;

  els.textContent.focus();
  els.textContent.select();
  els.textContent.scrollIntoView({ block: "nearest" });
  showToast("在右侧修改文字，点击“应用并刷新预览”查看真实替换效果。");
}

function renderLayers() {
  const project = state.current.project;
  els.layerList.replaceChildren();
  els.emptyLayerList.hidden = project.layers.length > 0;
  els.layerCount.textContent = `${project.layers.length} 层`;
  project.layers.slice().reverse().forEach((layer) => {
    const row = document.createElement("div");
    row.className = `layer-row${layer.id === state.selectedLayerId ? " selected" : ""}`;
    row.tabIndex = 0;
    row.addEventListener("click", () => selectLayer(layer.id));
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") selectLayer(layer.id); });
    let visibilityControl;
    if (layer.kind === "text" && layer.style.candidate_only) {
      visibilityControl = document.createElement("span");
      visibilityControl.className = `layer-state${layer.style.replacement_active ? " replaced" : ""}`;
      visibilityControl.textContent = layer.style.replacement_active ? "改" : "原";
      visibilityControl.title = layer.style.replacement_active ? "已替换文字" : "保留原稿文字，仍可直接点击修改";
    } else {
      visibilityControl = document.createElement("input");
      visibilityControl.type = "checkbox";
      visibilityControl.checked = layer.visible;
      visibilityControl.ariaLabel = `显示 ${layer.name}`;
      visibilityControl.addEventListener("click", (event) => event.stopPropagation());
      visibilityControl.addEventListener("change", () => { layer.visible = visibilityControl.checked; saveProject({ collect: false }); });
    }
    const icon = document.createElement("span");
    icon.className = "layer-icon";
    icon.textContent = layer.kind === "text" ? "T" : layer.kind === "vector" ? "V" : "R";
    const name = document.createElement("span");
    name.className = "layer-name";
    const title = document.createElement("b");
    title.textContent = layer.name;
    const detail = document.createElement("small");
    detail.textContent = layer.kind === "text"
      ? `${layer.style.candidate_only ? (layer.style.replacement_active ? "已替换 · " : "原稿文字 · ") : ""}${layer.content}`
      : layer.kind === "vector" ? "可缩放矢量" : "保留位图";
    name.append(title, detail);
    const confidence = document.createElement("span");
    confidence.className = `confidence${layer.confidence != null && layer.confidence < .88 ? " low" : ""}`;
    confidence.textContent = layer.confidence == null ? "" : `${Math.round(layer.confidence * 100)}%`;
    row.append(visibilityControl, icon, name, confidence);
    els.layerList.append(row);
  });
}

function renderQuality() {
  const quality = state.current.project.quality;
  const labels = { ready: "基础检查通过", review: "建议检查", refine: "建议人工精修" };
  els.qualityScore.textContent = quality.score;
  els.qualityVerdict.className = `verdict ${quality.verdict}`;
  els.qualityVerdict.textContent = labels[quality.verdict];
  const metricValues = [quality.metrics.text_count, quality.metrics.layer_count, quality.metrics.color_count, quality.metrics.node_count];
  $$("#metricGrid b").forEach((node, index) => { node.textContent = metricValues[index] ?? "--"; });
  els.warningList.replaceChildren();
  const warnings = quality.warnings.length ? quality.warnings : ["未发现明显风险，可继续检查并导出。"];
  warnings.forEach((text) => {
    const item = document.createElement("div");
    item.className = "warning-item";
    const copy = document.createElement("span");
    copy.textContent = text;
    item.append(copy);
    els.warningList.append(item);
  });
}

function selectLayer(id) {
  if (state.current && state.selectedLayerId !== id) collectEditor();
  state.selectedLayerId = id;
  renderLayers();
  renderEditor();
  renderCanvasTextTargets();
  if (typeof workbench !== "undefined") workbench.selection();
}

function currentLayer() {
  return state.current?.project.layers.find((layer) => layer.id === state.selectedLayerId) || null;
}

function renderEditor() {
  const layer = currentLayer();
  els.emptyEditor.hidden = Boolean(layer);
  els.layerEditor.hidden = !layer;
  if (!layer) {
    els.selectedLayerTitle.textContent = "未选择图层";
    return;
  }
  els.selectedLayerTitle.textContent = layer.name;
  const isText = layer.kind === "text";
  els.textContentLabel.hidden = !isText;
  els.fontFamilyLabel.hidden = !isText;
  $("#textTools").hidden = !isText;
  if (isText) {
    textFields.forEach(([id, key, fallback, type]) => {
      const value = layer.style[key] ?? (key === "font_size" ? Math.max(8, layer.bbox.height * .78) : fallback);
      $("#" + id)[type === "checkbox" ? "checked" : "value"] = value;
    });
    [["textX", "x"], ["textY", "y"], ["textWidth", "width"], ["textHeight", "height"]].forEach(([id, key]) => { $("#" + id).value = layer.bbox[key]; });
    updateTextBackgroundHint();
  }
  els.layerColorLabel.hidden = layer.kind === "raster";
  els.textContent.value = layer.content || "";
  els.fontFamily.value = layer.style.font_family || "Arial";
  els.layerColor.value = layer.style.fill_override || layer.style.fill || "#161616";
  els.layerOpacity.value = Math.round(layer.opacity * 100);
  els.restoreOriginal.hidden = !(isText && layer.style.candidate_only && layer.style.replacement_active);
}

function collectEditor(activateReplacement = false) {
  const layer = currentLayer();
  if (!layer) return;
  const opacity = Math.max(0, Math.min(1, Number(els.layerOpacity.value) / 100));
  if (layer.kind === "text") {
    let appearanceChanged = layer.content !== els.textContent.value || layer.style.font_family !== els.fontFamily.value || layer.style.fill !== els.layerColor.value;
    let changed = layer.content !== els.textContent.value || layer.style.font_family !== els.fontFamily.value
      || layer.style.fill !== els.layerColor.value || layer.opacity !== opacity;
    textFields.forEach(([id, key, fallback, type]) => {
      const control = $("#" + id);
      const value = type === "checkbox" ? control.checked : type === "number" ? Number(control.value) : control.value;
      const previous = layer.style[key] ?? (key === "font_size" ? Math.max(8, layer.bbox.height * .78) : fallback);
      if (value !== previous) { layer.style[key] = value; changed = true; if (!["background_mode", "background_color"].includes(key)) appearanceChanged = true; }
    });
    [["textX", "x"], ["textY", "y"], ["textWidth", "width"], ["textHeight", "height"]].forEach(([id, key]) => {
      const value = Number($("#" + id).value);
      if (value !== layer.bbox[key]) { layer.bbox[key] = value; changed = true; if (["width","height"].includes(key)) appearanceChanged = true; }
    });
    if (appearanceChanged) layer.style.appearance = "editable";
    layer.content = els.textContent.value;
    layer.style.font_family = els.fontFamily.value;
    layer.style.fill = els.layerColor.value;
    if ((activateReplacement || changed) && layer.style.candidate_only) {
      layer.style.replacement_active = true;
      layer.visible = true;
    }
  } else if (layer.kind === "vector" && (layer.style.fill_override || "#161616") !== els.layerColor.value) {
    layer.style.fill_override = els.layerColor.value;
  }
  layer.opacity = opacity;
}

function resetHistory() {
  state.undo = [];
  state.redo = [];
  state.committed = state.current ? JSON.stringify(state.current.project) : null;
  updateHistoryButtons();
}

function updateHistoryButtons() {
  $("#undoButton").disabled = state.busy || !state.undo.length;
  $("#redoButton").disabled = state.busy || !state.redo.length;
}

let saveJob = null;
async function saveProject({ activateReplacement = false, collect = true } = {}) {
  if (!state.current || state.busy) return false;
  if (collect) {
    if (!els.layerEditor.reportValidity()) return false;
    collectEditor(activateReplacement);
  }
  if (saveJob) return saveJob;
  saveJob = (async () => {
    try {
      while (state.current && JSON.stringify(state.current.project) !== state.committed) {
        const before = state.committed;
        const snapshot = JSON.stringify(state.current.project);
        const project = JSON.parse(snapshot);
        if (typeof workbench !== "undefined") workbench.status("更新中…");
        const result = await api(`/api/projects/${project.id}`, { project, base_revision: project.revision || 0 });
        const newer = JSON.stringify(state.current.project) !== snapshot || (typeof workbench !== "undefined" && workbench.composing);
        if (before) state.undo.push(before);
        state.undo = state.undo.slice(-30);
        state.redo = [];
        state.committed = JSON.stringify(result.project);
        if (newer) {
          state.current.project.revision = result.project.revision;
          state.current.preview_url = result.preview_url;
        } else {
          state.current = result;
          renderProject({ preserveEditor: true });
        }
      }
      if (typeof workbench !== "undefined") workbench.status("已保存");
      return true;
    } catch (error) {
      if (typeof workbench !== "undefined") workbench.status("保存失败，点击重试", true);
      showToast(`保存失败，当前输入已保留：${error.message}`, true);
      return false;
    } finally {
      updateHistoryButtons();
    }
  })();
  try { return await saveJob; } finally { saveJob = null; }
}

async function travelHistory(redo = false) {
  if (state.current && !state.busy && JSON.stringify(state.current.project) !== state.committed) {
    if (!await saveProject({collect:false})) return;
  }
  const source = redo ? state.redo : state.undo;
  const destination = redo ? state.undo : state.redo;
  if (state.busy || !source.length) return;
  setBusy(true, redo ? "正在重做…" : "正在撤销…");
  try {
    const project = JSON.parse(source[source.length - 1]);
    const result = await api(`/api/projects/${project.id}`, { project, base_revision: state.current.project.revision || 0 });
    source.pop();
    destination.push(state.committed);
    state.current = result;
    state.committed = JSON.stringify(result.project);
    if (!currentLayer()) state.selectedLayerId = result.project.layers[0]?.id || null;
    renderProject();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function showRecentProjects() {
  if (state.busy) return;
  if (state.current && !await saveProject()) return;
  try {
    const result = await apiGet("/api/projects");
    const list = $("#recentProjectsList");
    list.replaceChildren();
    $("#recentProjectCount").textContent = `${result.projects.length} 份`;
    if (!result.projects.length) {
      const empty = document.createElement("p"); empty.className = "dialog-empty";
      empty.textContent = "还没有工程。导入图片并处理后，会自动保存在这里。"; list.append(empty);
    }
    result.projects.forEach((project) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "recent-project-card";
      const date = new Date(project.updated_at * 1000).toLocaleString();
      button.ariaLabel = `${project.name} · ${modeLabel(project.mode)} · ${date}`;
      const image = document.createElement("img"); image.alt = ""; image.loading = "lazy"; image.decoding = "async";
      image.src = `/api/projects/${encodeURIComponent(project.id)}/files/preview.png?t=${project.updated_at}`;
      image.onerror = () => { image.hidden = true; };
      const thumbnail = document.createElement("span"); thumbnail.className = "recent-thumbnail"; thumbnail.append(image);
      const info = document.createElement("span"); info.className = "recent-project-info";
      const name = document.createElement("strong"); name.textContent = project.name;
      const detail = document.createElement("span"); detail.className = "recent-project-meta";
      const mode = document.createElement("span"); mode.className = "recent-mode"; mode.textContent = modeLabel(project.mode);
      const time = document.createElement("time"); time.dateTime = new Date(project.updated_at * 1000).toISOString(); time.textContent = date;
      detail.append(mode, time); info.append(name, detail);
      const open = document.createElement("span"); open.className = "recent-open"; open.textContent = "打开 →";
      button.append(thumbnail, info, open);
      button.addEventListener("click", async () => {
        $("#recentProjectsDialog").close();
        setBusy(true, "正在打开工程…");
        try {
          const opened = await apiGet(`/api/projects/${project.id}`);
          state.current = opened;
          const modeInput = $$('input[name="mode"]').find(input => input.value === opened.project.mode);
          if (modeInput) { modeInput.checked = true; updateModeUI(); }
          state.selectedLayerId = opened.project.layers.find(layer => layer.kind === "text")?.id || opened.project.layers[0]?.id;
          resetHistory();
          renderProject();
          showToast(`已打开：${project.name}`);
        } catch (error) {
          showToast(error.message, true);
        } finally {
          setBusy(false);
        }
      });
      list.append(button);
    });
    $("#recentProjectsDialog").showModal();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function restoreOriginalText() {
  const layer = currentLayer();
  if (!layer || !layer.style.candidate_only) return;
  layer.content = layer.style.original_content || layer.content;
  if (layer.style.original_bbox) layer.bbox = { ...layer.style.original_bbox };
  layer.style.appearance = "original";
  layer.style.repair_strokes = [];
  layer.style.replacement_active = false;
  layer.visible = false;
  renderEditor();
  if (await saveProject({ collect: false })) showToast("已恢复原稿文字。", false);
}

function moveLayer(direction) {
  if (state.busy) return;
  const layers = state.current?.project.layers;
  if (!layers) return;
  const index = layers.findIndex((layer) => layer.id === state.selectedLayerId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= layers.length) return;
  [layers[index], layers[target]] = [layers[target], layers[index]];
  saveProject({ collect: false });
}

async function exportPackage() {
  if (state.busy || !state.current) return;
  const formats = $$(".format-checks input:checked").map((input) => input.value);
  if (!formats.length) {
    showToast("请至少选择一种导出内容。", true);
    return;
  }
  const destination = await chooseExportDirectory();
  if (destination === null || !await saveProject()) return;
  setBusy(true, "正在生成商用素材包…");
  try {
    const result = await api(`/api/projects/${state.current.project.id}/export`, { formats, destination });
    const note = "素材包已生成";
    showToast(`${note}\n${result.destination}`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function chooseExportDirectory() {
  const dialog = $("#exportDirectoryDialog");
  if (dialog.open) return null;
  const input = $("#exportDirectoryPath");
  const browse = $("#browseExportDirectory");
  try {
    input.value = (await apiGet("/api/export-directory")).directory;
  } catch (error) {
    showToast(error.message, true);
    return null;
  }
  browse.hidden = !window.pywebview?.api?.choose_export_directory;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "export" ? input.value.trim() : null), { once: true });
  });
}

$("#exportDirectoryForm").addEventListener("submit", event => {
  event.preventDefault();
  if ($("#exportDirectoryPath").value.trim()) $("#exportDirectoryDialog").close("export");
});
$("#cancelExportDirectory").addEventListener("click", () => $("#exportDirectoryDialog").close());
$("#browseExportDirectory").addEventListener("click", async () => {
  const button = $("#browseExportDirectory");
  button.disabled = true;
  try {
    const path = await window.pywebview.api.choose_export_directory($("#exportDirectoryPath").value);
    if (path) $("#exportDirectoryPath").value = path;
  } catch (error) {
    showToast("无法打开文件夹选择器，请直接填写完整路径。", true);
  } finally {
    button.disabled = false;
  }
});

function setView(view) {
  if (view !== "result" && typeof livePreview !== "undefined") livePreview.clear();
  state.view = view;
  $$("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  if (!state.current) return;
  els.compareControl.hidden = view !== "compare";
  if (view === "original") {
    els.resultImage.style.clipPath = "inset(0 0 0 100%)";
    els.comparisonLine.hidden = true;
  } else if (view === "result") {
    els.resultImage.style.clipPath = "inset(0)";
    els.comparisonLine.hidden = true;
  } else {
    els.comparisonLine.hidden = false;
    updateCompare();
  }
}

function updateCompare() {
  const value = Number(els.compareRange.value);
  els.resultImage.style.clipPath = `inset(0 0 0 ${value}%)`;
  els.comparisonLine.style.left = `${value}%`;
}

function setZoom(zoom) {
  state.zoom = zoom;
  if (state.current) els.imageBoard.style.width = `${state.current.project.canvas.width}px`;
  els.imageBoard.style.zoom = zoom;
  if (typeof workbench !== "undefined") workbench.syncScale();
  $$("[data-zoom]").forEach((button) => button.classList.toggle("active", Number(button.dataset.zoom) === zoom));
}

function updateModeUI() {
  const mode = selectedMode();
  $$(".mode-option").forEach((label) => label.classList.toggle("selected", label.querySelector("input").checked));
  els.batchSettings.hidden = mode !== "format";
  els.traceSettings.hidden = mode !== "logo";
  els.fileInput.multiple = mode === "format";
  els.process.textContent = { marketing: "识别文字并开始编辑", logo: "开始转矢量", format: "选择位置并转换", cleanup: "打开图片并选区" }[mode];
  $("#modeHint").textContent = {
    marketing: "识别文字并保留底图，不会还原原始设计文件的全部图层。",
    logo: "适合 Logo、图标和简单插画；复杂照片、渐变及细小文字可能失真。",
    format: "可处理单张或多张图片；只转换格式与尺寸，不识别文字或生成矢量。",
    cleanup: "手动涂抹或框选水印、日期和杂物，本地修补并保留原图。",
  }[mode];
  if (mode !== "format" && state.files.length > 1) {
    state.files = state.files.slice(0, 1);
    els.fileInput.value = "";
    els.chooseFileText.textContent = state.files[0].name;
    showToast("此功能一次处理一张图片，已保留第 1 张。");
  }
}

function resetProject() {
  state.files = [];
  state.current = null;
  resetHistory();
  state.selectedLayerId = null;
  els.fileInput.value = "";
  if (typeof workbench !== "undefined") workbench.clearFiles();
  els.chooseFileText.textContent = "选择图片";
  els.process.disabled = true;
  els.export.disabled = true;
  els.emptyStage.hidden = false;
  els.compareStage.hidden = true;
  els.compareControl.hidden = true;
  els.textOverlayLayer.replaceChildren();
  els.canvasEditHint.hidden = true;
  els.layerList.replaceChildren();
  els.emptyLayerList.hidden = false;
  els.layerCount.textContent = "0 层";
  els.recommendation.textContent = "本地处理";
  els.qualityScore.textContent = "--";
  els.qualityVerdict.className = "verdict neutral";
  els.qualityVerdict.textContent = "等待处理";
  renderEditor();
}

let busyTimer;
function setBusy(active, text = "") {
  clearInterval(busyTimer);
  $("#cancelTaskButton").disabled = false;
  if (active) {
    const started = Date.now();
    const progress = () => { $("#busyProgress").textContent = `已用时 ${Math.floor((Date.now() - started) / 1000)} 秒 · 后台任务最长 180 秒，可取消`; };
    progress();
    busyTimer = setInterval(progress, 1000);
  }
  state.busy = active;
  if (typeof workbench !== "undefined" && active) workbench.status(text || "处理中…");
  updateHistoryButtons();
  els.busy.hidden = !active;
  if (text) els.busyText.textContent = text;
}

let toastTimer;
function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.className = `toast show${isError ? " error" : ""}`;
  toastTimer = setTimeout(() => { els.toast.className = "toast"; }, isError ? 6000 : 5000);
}

function modeLabel(mode) {
  return { marketing: "修改图片文字", logo: "图片转矢量", format: "格式与尺寸转换", cleanup: "去除水印／杂物" }[mode] || mode;
}

els.fileInput.addEventListener("change", (event) => {
  if (typeof workbench !== "undefined") workbench.importFiles(event.target.files); else setFiles(event.target.files);
});
els.dropzone.addEventListener("dragover", (event) => { event.preventDefault(); els.dropzone.classList.add("dragging"); });
els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("dragging"));
els.dropzone.addEventListener("drop", (event) => { event.preventDefault(); els.dropzone.classList.remove("dragging"); if (typeof workbench !== "undefined") workbench.importFiles(event.dataTransfer.files); else setFiles(event.dataTransfer.files); });
els.process.addEventListener("click", processFiles);
els.export.addEventListener("click", exportPackage);
$("#cancelTaskButton").addEventListener("click", async () => {
  $("#cancelTaskButton").disabled = true;
  try {
    await api("/api/cancel", {});
    els.busyText.textContent = "正在终止后台任务…";
  } catch (error) {
    showToast(error.message, true);
    $("#cancelTaskButton").disabled = false;
  }
});
els.newProject.addEventListener("click", async () => {
  if (!state.busy && (!state.current || await saveProject())) resetProject();
});
$("#recentProjectsButton").addEventListener("click", showRecentProjects);
$("#undoButton").addEventListener("click", () => travelHistory());
$("#redoButton").addEventListener("click", () => travelHistory(true));
document.addEventListener("keydown", event => {
  if (!(event.ctrlKey || event.metaKey) || event.target.closest("input, textarea, select, [contenteditable]")) return;
  if (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y") {
    event.preventDefault();
    travelHistory(event.shiftKey || event.key.toLowerCase() === "y");
  }
});
els.compareRange.addEventListener("input", updateCompare);
$$('[data-view]').forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
$$('[data-zoom]').forEach((button) => button.addEventListener("click", () => setZoom(Number(button.dataset.zoom))));
$$('input[name="mode"]').forEach((input) => input.addEventListener("change", updateModeUI));
$("#colorPrecision").addEventListener("input", (event) => { $("#colorPrecisionOutput").textContent = event.target.value; });
$("#speckle").addEventListener("input", (event) => { $("#speckleOutput").textContent = event.target.value; });
$("#applyChangesButton").addEventListener("click", () => saveProject());
$("#textBackground").addEventListener("change", updateTextBackgroundHint);
els.layerEditor.addEventListener("submit", event => event.preventDefault());
els.restoreOriginal.addEventListener("click", restoreOriginalText);
$("#moveUpButton").addEventListener("click", () => moveLayer(1));
$("#moveDownButton").addEventListener("click", () => moveLayer(-1));
$("#deleteLayerButton").addEventListener("click", () => {
  const layer = currentLayer();
  if (!layer || !confirm(`确认删除“${layer.name}”？`)) return;
  state.current.project.layers = state.current.project.layers.filter((item) => item.id !== layer.id);
  state.selectedLayerId = state.current.project.layers[0]?.id || null;
  saveProject({ collect: false });
});

updateModeUI();
initializeApp();

$("#closeExportTop").addEventListener("click", () => $("#cancelExportDirectory").click());
$("#closeRepairTop").addEventListener("click", () => $("#closeRepair").click());
