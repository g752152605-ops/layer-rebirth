// Canvas interaction stays local; only completed gestures are persisted.
const workbench = {
  urls: [], assets: null, assetJob: null, importSerial: 0, timer: null,
  composing: false, dragging: null, repair: null, repairBusy: false, space: false,
  status(message, failed = false) {
    $("#saveStatus").textContent = message;
    $("#saveStatus").classList.toggle("failed", failed);
  },
  clearFiles() {
    if (typeof livePreview !== "undefined") livePreview.clear();
    this.urls.forEach(url => URL.revokeObjectURL(url));
    this.urls = [];
    $("#imageCards").replaceChildren();
    document.body.classList.remove("has-image");
    this.assets = null;
  },
  async importFiles(files) {
    if (state.busy || this.dragging || this.repairBusy) return;
    const list = Array.from(files);
    if (!list.some(f => ["image/png","image/jpeg","image/webp","image/bmp"].includes(f.type) && f.size <= 50*1024*1024)) {
      showToast("请选择 50 MB 以内的图片。", true); return;
    }
    if (state.current && !await saveProject()) return;
    const previous = state.files;
    setFiles(list);
    if (state.files === previous) return;
    state.current = null;
    state.selectedLayerId = null;
    resetHistory();
    els.export.disabled = true;
    els.textOverlayLayer.replaceChildren();
    els.layerList.replaceChildren();
    els.layerCount.textContent="0 层";
    els.warningList.textContent="识别后列出需要检查的文字。";
    renderEditor();
    this.status("待处理 · 原图预览");
  },
  previewFiles() {
    this.clearFiles();
    const token = ++this.importSerial;
    const cards = $("#imageCards");
    state.files.forEach((file, index) => {
      const url = typeof file.data === "string" ? file.data : URL.createObjectURL(file);
      if (!file.data) this.urls.push(url);
      const card = document.createElement("button"); card.type = "button"; card.className = "image-card";
      const image = document.createElement("img"); image.src = url; image.alt = file.name;
      const label = document.createElement("span"); label.textContent = file.name;
      const size = document.createElement("small"); size.textContent = "读取尺寸…";
      image.onload = () => {
        if (token !== this.importSerial) return;
        size.textContent = `${image.naturalWidth} × ${image.naturalHeight} · 待处理`;
        if (!state.current && card.classList.contains("selected")) {
          els.imageBoard.style.width = `${Math.min(image.naturalWidth,$("#stageScroll").clientWidth-48)}px`;
          els.imageBoard.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
        }
      };
      card.append(image, label, size);
      const display = () => {
        cards.querySelectorAll(".image-card").forEach(node => node.classList.remove("selected")); card.classList.add("selected");
        els.originalImage.src = url; els.resultImage.src = url;
        els.resultImage.style.clipPath = "none"; els.comparisonLine.hidden = true;
        els.emptyStage.hidden = true; els.compareStage.hidden = false;
        els.textOverlayLayer.replaceChildren();
        els.imageBoard.style.width = "100%"; els.imageBoard.style.zoom = 1;
        els.imageBoard.style.aspectRatio = "auto";
        document.body.classList.add("has-image");
      };
      card.addEventListener("click", () => { if (!state.current && !state.busy) display(); });
      cards.append(card);
      if (!index) display();
    });
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "button remove-image"; remove.textContent = "移除图片";
    remove.onclick = async () => { if (!state.busy && (!state.current || await saveProject())) resetProject(); };
    cards.append(remove);
  },
  render() {
    if (!state.current) return;
    document.body.classList.add("has-image");
    const cards = $("#imageCards");
    const image = document.createElement("img"); image.src = state.current.original_url; image.alt = state.current.project.name;
    const text = document.createElement("span"); text.textContent = state.current.project.name;
    const size = document.createElement("small"); size.textContent = `${state.current.project.canvas.width} × ${state.current.project.canvas.height} · 可编辑`;
    const card = document.createElement("div"); card.className = "image-card selected"; card.append(image,text,size);
    const remove = document.createElement("button"); remove.className = "button"; remove.textContent = "移除图片";
    remove.onclick = async () => { if (!state.busy && await saveProject()) resetProject(); };
    cards.replaceChildren(card,remove);
    this.assets = null;
    $("#dragObject").hidden = true;
    this.selection(true);
    if (typeof livePreview !== "undefined") livePreview.settled();
    this.renderIssues();
    if (this.lastProject !== state.current.project.id) { this.lastProject=state.current.project.id; this.fit(); }
    this.status("已保存");
  },
  selection(keepPreview = false) {
    if (!keepPreview && typeof livePreview !== "undefined") livePreview.clear();
    const layer = currentLayer();
    $("#repairButton").hidden = layer?.kind !== "text";
    $("#appearanceLabel").textContent = layer?.kind === "text"
      ? layer.style.appearance === "original" ? "原字形 · 可直接拖动；改字或字体后使用近似字体" : "可编辑文字 · 可拖动与排版" : "";
    clearTimeout(this.prefetchTimer);
    if (layer?.kind === "text") this.prefetchTimer=setTimeout(()=>this.prepareAssets().catch(() => {}),180);
  },
  renderIssues() {
    els.warningList.replaceChildren();
    if (state.current.project.mode === "cleanup") {
      els.warningList.textContent = "请对比检查修补边缘与背景纹理；复杂背景可能模糊，必要时缩小选区或保护重要细节。";
      return;
    }
    state.current.project.layers.filter(l => l.kind === "text" && (l.style.repair_warning || l.confidence < .88)).forEach(layer => {
      const button = document.createElement("button"); button.type = "button"; button.className = "issue-item";
      button.textContent = `${layer.content}：${layer.style.repair_warning || "识别置信度偏低，请核对"}`;
      button.onclick = () => { selectLayer(layer.id); setView("result"); };
      els.warningList.append(button);
    });
    if (!els.warningList.children.length) els.warningList.textContent = "未发现明显风险。请检查原字残留、字体与边缘后导出。";
  },
  schedule() {
    if (this.composing || state.busy || !state.current) return;
    clearTimeout(this.timer);
    // Update the draft immediately so in-flight saves cannot overwrite typing.
    if (!els.layerEditor.checkValidity()) { this.status("请检查输入范围"); return; }
    collectEditor();
    if (typeof livePreview !== "undefined") livePreview.update();
    this.status("预览中 · 待保存");
    this.timer = setTimeout(() => saveProject({collect:false}), 400);
  },
  async prepareAssets() {
    const layer = currentLayer();
    if (!layer || layer.kind !== "text" || !state.current) return null;
    const id = state.current.project.id, revision = state.current.project.revision || 0;
    const key = `${id}:${revision}:${layer.id}`;
    if (this.assets?.key === key) return this.assets;
    if (this.assetJob?.key === key) return this.assetJob.promise;
    const promise = (async () => {
      const result = await api(`/api/projects/${id}/text/${encodeURIComponent(layer.id)}/drag-assets`, {base_revision:revision});
      const [baseImage, objectImage] = await Promise.all([this.loadImage(result.base_url),this.loadImage(result.object_url)]);
      const assets = {...result,key,baseImage,objectImage};
      if (state.current?.project.id === id && (state.current.project.revision || 0) === revision && currentLayer()?.id === layer.id) this.assets = assets;
      return assets;
    })();
    this.assetJob = {key,promise};
    const clear=()=>{if(this.assetJob?.promise===promise)this.assetJob=null;};
    promise.then(clear,clear);
    return promise;
  },
  loadImage(url) { return new Promise((resolve,reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("预览素材读取失败")); image.src=url; }); },
  bindText(target, layer) {
    target.title = "单击选中 · 拖动移动 · 双击改字 · 方向键微移";
    target.addEventListener("click", event => event.preventDefault());
    target.addEventListener("dblclick", () => beginCanvasTextEdit(layer.id));
    target.addEventListener("pointerdown", event => this.startDrag(event,target,layer));
    target.addEventListener("keydown", event => {
      const delta = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];
      if (!delta || state.busy) return;
      event.preventDefault();
      state.selectedLayerId = layer.id;
      const selected = currentLayer();
      selected.bbox.x += delta[0]*(event.shiftKey ? 10 : 1); selected.bbox.y += delta[1]*(event.shiftKey ? 10 : 1);
      selected.visible = true; selected.style.replacement_active = true;
      target.style.left=`${selected.bbox.x/state.current.project.canvas.width*100}%`;
      target.style.top=`${selected.bbox.y/state.current.project.canvas.height*100}%`;
      renderEditor(); setView("result"); saveProject({collect:false});
    });
  },
  startDrag(event,target,layer) {
    if (event.button !== 0 || this.space || state.busy || saveJob || this.dragging) return;
    event.preventDefault(); event.stopPropagation();
    collectEditor();
    if (JSON.stringify(state.current.project) !== state.committed) {
      saveProject({collect:false});this.status("正在保存输入，请稍后拖动");return;
    }
    state.selectedLayerId=layer.id; renderEditor(); this.selection();
    target.focus(); target.setPointerCapture(event.pointerId);
    const box={...layer.bbox}, rect=els.imageBoard.getBoundingClientRect(), canvas=state.current.project.canvas;
    const gesture={target,layer,box,startX:event.clientX,startY:event.clientY,dx:0,dy:0,moved:false,assets:null};
    this.dragging=gesture;
    const move = e => {
      const px=e.clientX-gesture.startX, py=e.clientY-gesture.startY;
      if (!gesture.moved && Math.hypot(px,py)<4) return;
      gesture.moved=true; setView("result");
      if(!gesture.loading) {
        gesture.loading=true;
        this.prepareAssets().then(assets=>{if(this.dragging===gesture){gesture.assets=assets;this.paintDrag(gesture);}})
          .catch(error=>{if(this.dragging===gesture){this.status("拖动预览未就绪，请重试",true);gesture.error=error;}});
      }
      let x=box.x+px*canvas.width/rect.width,y=box.y+py*canvas.height/rect.height;
      $("#guideX").hidden=$("#guideY").hidden=true;
      if (!e.altKey) {
        const others=state.current.project.layers.filter(l=>l.id!==layer.id && l.kind==="text");
        const xs=[canvas.width/2,...others.flatMap(l=>[l.bbox.x,l.bbox.x+l.bbox.width])];
        const ys=[canvas.height/2,...others.flatMap(l=>[l.bbox.y,l.bbox.y+l.bbox.height])];
        for (const axis of ["x","y"]) {
          const positions=axis==="x"?xs:ys, value=axis==="x"?x:y, size=axis==="x"?box.width:box.height;
          const tolerance=5*(axis==="x"?canvas.width/rect.width:canvas.height/rect.height);
          let best=null;
          for(const anchor of positions) for(const offset of [0,size/2,size]) {
            const d=anchor-value-offset; if(Math.abs(d)<tolerance && (!best || Math.abs(d)<Math.abs(best.d))) best={d,anchor};
          }
          if(best) { if(axis==="x") x+=best.d; else y+=best.d;
            const guide=$(axis==="x"?"#guideX":"#guideY"); guide.hidden=false; guide.style[axis==="x"?"left":"top"]=`${best.anchor/(axis==="x"?canvas.width:canvas.height)*100}%`; }
        }
      }
      gesture.dx=x-box.x;gesture.dy=y-box.y;
      target.style.left=`${x/canvas.width*100}%`;target.style.top=`${y/canvas.height*100}%`;
      this.paintDrag(gesture);
    };
    const finish = async (cancel=false) => {
      if(this.dragging!==gesture) return;
      this.dragging=null;
      target.removeEventListener("pointermove",move); target.removeEventListener("pointerup",up); target.removeEventListener("pointercancel",cancelled);
      if(target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
      $("#guideX").hidden=$("#guideY").hidden=true;
      if(!cancel && gesture.moved && !gesture.error) {
        const selected=currentLayer();
        selected.bbox={...box,x:box.x+gesture.dx,y:box.y+gesture.dy};
        selected.visible=true;selected.style.replacement_active=true;
        renderEditor(); await saveProject({collect:false});
      }
      $("#dragObject").hidden=true;els.resultImage.src=state.current.preview_url;
      renderCanvasTextTargets();
      const node=Array.from(els.textOverlayLayer.children).find(n=>n.dataset.layerId===layer.id);node?.focus();
    };
    const up=()=>finish(false),cancelled=()=>finish(true);
    gesture.cancel=cancelled;
    target.addEventListener("pointermove",move);target.addEventListener("pointerup",up);target.addEventListener("pointercancel",cancelled);
  },
  paintDrag(g) {
    if(!g.assets) { this.status("准备文字预览…"); return; }
    els.resultImage.src=g.assets.base_url;
    const overlay=$("#dragObject");overlay.src=g.assets.object_url;overlay.hidden=false;
    const c=state.current.project.canvas;
    overlay.style.transform=`translate(${g.dx/c.width*100}%, ${g.dy/c.height*100}%)`;
  },
  async openRepair() {
    if(state.busy || !currentLayer() || !await saveProject()) return;
    this.repair={id:currentLayer().id,stroke:null};
    $("#repairDialog").showModal();
    await this.refreshRepair();
  },
  async refreshRepair() {
    this.repairBusy=true;$("#repairStatus").textContent="准备局部预览…";
    try {
      this.assets=null;
      const assets=await this.prepareAssets();
      const [mask,result]=await Promise.all([this.loadImage(assets.mask_url),this.loadImage(state.current.preview_url)]);
      Object.assign(this.repair,{assets,mask,result,box:assets.repair_bbox});
      this.drawRepair();$("#repairStatus").textContent="红色是清除区域；绿色笔画用于保护。每笔都可撤销。";
    } catch(error) { $("#repairStatus").textContent=error.message; }
    finally {this.repairBusy=false;}
  },
  drawRepair() {
    const r=this.repair;if(!r?.box)return;
    const c=$("#repairCanvas"),b=r.box;
    c.width=Math.min(1200,Math.max(400,b.width*2));c.height=Math.round(c.width*b.height/b.width);
    const ctx=c.getContext("2d"),source=$("#repairBaseOnly").checked?r.assets.baseImage:r.result;
    const scale=source.naturalWidth/state.current.project.canvas.width;
    ctx.drawImage(source,b.x*scale,b.y*scale,b.width*scale,b.height*scale,0,0,c.width,c.height);
    if($("#showMask").checked) {
      const tint=document.createElement("canvas");tint.width=r.mask.naturalWidth;tint.height=r.mask.naturalHeight;
      const t=tint.getContext("2d");t.drawImage(r.mask,0,0);const pixels=t.getImageData(0,0,tint.width,tint.height);
      for(let i=0;i<pixels.data.length;i+=4) { const alpha=pixels.data[i];pixels.data[i]=220;pixels.data[i+1]=65;pixels.data[i+2]=40;pixels.data[i+3]=alpha*.4; }
      t.putImageData(pixels,0,0);ctx.drawImage(tint,0,0,c.width,c.height);
      ctx.save();ctx.strokeStyle="rgba(41,148,110,.4)";ctx.lineCap="round";ctx.lineJoin="round";
      for(const stroke of currentLayer().style.repair_strokes || []) {
        if(stroke.mode!=="protect")continue;
        ctx.lineWidth=stroke.radius*2*c.width/b.width;ctx.beginPath();
        stroke.points.forEach(([x,y],i)=>ctx[i?"lineTo":"moveTo"]((x-b.x)/b.width*c.width,(y-b.y)/b.height*c.height));
        if(stroke.points.length===1) { const [x,y]=stroke.points[0];ctx.lineTo((x-b.x)/b.width*c.width+.01,(y-b.y)/b.height*c.height); }
        ctx.stroke();
      }
      ctx.restore();
    }
    if(r.stroke) {
      ctx.strokeStyle=r.stroke.mode==="protect"?"#29946e":"#da523a";ctx.lineWidth=r.stroke.radius*2*c.width/b.width;ctx.lineCap="round";ctx.lineJoin="round";
      ctx.beginPath();r.stroke.points.forEach(([x,y],i)=>ctx[i?"lineTo":"moveTo"]((x-b.x)/b.width*c.width,(y-b.y)/b.height*c.height));ctx.stroke();
    }
  },
  async commitRepair(strokes) {
    if(this.repairBusy)return;
    this.repairBusy=true;$("#repairStatus").textContent="正在修补…";
    const before=state.committed;
    try {
      const p=state.current.project;
      const result=await api(`/api/projects/${p.id}/text/${encodeURIComponent(this.repair.id)}/repair`,{base_revision:p.revision||0,strokes});
      if(before)state.undo.push(before);state.undo=state.undo.slice(-30);state.redo=[];
      state.current=result;state.committed=JSON.stringify(result.project);renderProject();
      await this.refreshRepair();this.status("已保存");
    } catch(error) { $("#repairStatus").textContent=`修补失败：${error.message}，可重新画笔重试。`; }
    finally {this.repairBusy=false;}
  },
  fit() {
    if(!state.current)return;
    const stage=$("#stageScroll"),c=state.current.project.canvas;
    const scale=Math.min((stage.clientWidth-48)/c.width,(stage.clientHeight-48)/c.height,1);
    els.imageBoard.style.width=`${c.width}px`;state.zoom=scale;els.imageBoard.style.zoom=scale;
    this.syncScale();
  },
  syncScale() {
    if(!state.current)return;
    const scale=els.imageBoard.getBoundingClientRect().width/state.current.project.canvas.width;
    if(!scale)return;
    els.comparisonLine.style.width=`${24/scale}px`;els.comparisonLine.style.marginLeft=`${-12/scale}px`;
    const handle=els.comparisonLine.querySelector("span");
    handle.style.width=`${28/scale}px`;handle.style.height=`${36/scale}px`;handle.style.fontSize=`${14/scale}px`;
  },
};

els.layerEditor.addEventListener("compositionstart",()=>{workbench.composing=true;clearTimeout(workbench.timer);});
els.layerEditor.addEventListener("compositionend",()=>{workbench.composing=false;workbench.schedule();});
els.layerEditor.addEventListener("input",()=>workbench.schedule());
els.layerEditor.addEventListener("change",()=>workbench.schedule());
$("#saveStatus").onclick=()=>saveProject();
$("#repairButton").onclick=()=>workbench.openRepair();
$("#fitCanvas").onclick=()=>workbench.fit();
$("#closeRepair").onclick=()=>{if(!workbench.repairBusy){workbench.repair?.cancelStroke?.();$("#repairDialog").close();}};
$("#repairDialog").addEventListener("cancel",event=>{if(workbench.repairBusy)event.preventDefault();});
$("#resetRepair").onclick=()=>workbench.commitRepair([]);
$("#undoRepair").onclick=async()=>{if(workbench.repairBusy)return;await travelHistory();await workbench.refreshRepair();};
$("#showMask").onchange=$("#repairBaseOnly").onchange=()=>workbench.drawRepair();
$("#repairCanvas").addEventListener("pointerdown",event=>{
  if(workbench.repairBusy || !workbench.repair?.box || event.button!==0)return;
  const canvas=event.currentTarget,r=workbench.repair,b=r.box,rect=canvas.getBoundingClientRect();
  const point=e=>[Math.max(b.x,Math.min(b.x+b.width,b.x+(e.clientX-rect.left)/rect.width*b.width)),Math.max(b.y,Math.min(b.y+b.height,b.y+(e.clientY-rect.top)/rect.height*b.height))];
  r.stroke={mode:$("input[name='brush']:checked").value,radius:Number($("#brushRadius").value),points:[point(event)]};
  canvas.setPointerCapture(event.pointerId);
  const move=e=>{if(r.stroke.points.length<2000)r.stroke.points.push(point(e));workbench.drawRepair();};
  const finish=cancel=>{
    if(!r.stroke)return;
    canvas.removeEventListener("pointermove",move);canvas.removeEventListener("pointerup",up);canvas.removeEventListener("pointercancel",stop);
    if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
    const stroke=r.stroke;r.stroke=null;
    if(!cancel)workbench.commitRepair([...(currentLayer().style.repair_strokes||[]),stroke]);else workbench.drawRepair();
  };
  const up=()=>finish(false),stop=()=>finish(true);
  r.cancelStroke=stop;
  canvas.addEventListener("pointermove",move);canvas.addEventListener("pointerup",up);canvas.addEventListener("pointercancel",stop);
});

function compareAt(clientX) {
  const rect=els.imageBoard.getBoundingClientRect();
  els.compareRange.value=String(Math.max(0,Math.min(100,(clientX-rect.left)/rect.width*100)));updateCompare();
  els.comparisonLine.setAttribute("aria-valuenow",String(Math.round(Number(els.compareRange.value))));
}
els.comparisonLine.addEventListener("pointerdown",event=>{
  if(event.button!==0)return;
  event.preventDefault();event.stopPropagation();const line=els.comparisonLine;line.setPointerCapture(event.pointerId);
  const move=e=>compareAt(e.clientX);const end=()=>{line.removeEventListener("pointermove",move);line.removeEventListener("pointerup",end);line.removeEventListener("pointercancel",end);};
  line.addEventListener("pointermove",move);line.addEventListener("pointerup",end);line.addEventListener("pointercancel",end);
});
els.comparisonLine.addEventListener("dblclick",()=>{els.compareRange.value="50";updateCompare();els.comparisonLine.setAttribute("aria-valuenow","50");});
els.comparisonLine.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;event.preventDefault();const old=Number(els.compareRange.value);els.compareRange.value=String(event.key==="Home"?0:event.key==="End"?100:old+(event.key==="ArrowLeft"?-1:1)*(event.shiftKey?10:1));updateCompare();els.comparisonLine.setAttribute("aria-valuenow",els.compareRange.value);});
document.addEventListener("keydown",event=>{
  if(event.key==="Escape" && workbench.repair?.stroke){event.preventDefault();workbench.repair.cancelStroke();}
  if(event.key==="Escape" && workbench.dragging){event.preventDefault();workbench.dragging.cancel();}
  if(event.code==="Space" && !event.target.closest("input,textarea,select")){event.preventDefault();workbench.space=true;$("#stageScroll").classList.add("panning");}
});
document.addEventListener("keyup",event=>{if(event.code==="Space"){workbench.space=false;$("#stageScroll").classList.remove("panning");}});
$("#stageScroll").addEventListener("pointerdown",event=>{
  if(!workbench.space)return;event.preventDefault();const stage=event.currentTarget,x=event.clientX,y=event.clientY,sx=stage.scrollLeft,sy=stage.scrollTop;stage.setPointerCapture(event.pointerId);
  const move=e=>{stage.scrollLeft=sx+x-e.clientX;stage.scrollTop=sy+y-e.clientY;};const end=()=>{stage.removeEventListener("pointermove",move);stage.removeEventListener("pointerup",end);stage.removeEventListener("pointercancel",end);};
  stage.addEventListener("pointermove",move);stage.addEventListener("pointerup",end);stage.addEventListener("pointercancel",end);
});
