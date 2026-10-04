const cleanupTool = {
  session: null, busy: false,
  renderAction() {
    const active = state.current?.project.mode === "cleanup";
    $("#openCleanup").hidden = !active;
    if (active) els.layerEditor.hidden = true;
  },
  async open() {
    if (state.busy || this.busy || !state.current || !await saveProject()) return;
    const p = state.current.project;
    if (p.mode !== "cleanup") return;
    if (this.session?.id !== p.id || this.session.revision !== p.revision) {
      const strokes = structuredClone(p.layers[0].style.cleanup_strokes || []);
      this.session = {id:p.id, revision:p.revision, strokes, selectionUndo:[], stroke:null};
    }
    $("#cleanupDialog").showModal();
    await this.load();
  },
  async load() {
    this.setBusy(true, "正在读取图片…");
    try {
      const [original, result] = await Promise.all([
        workbench.loadImage(state.current.original_url), workbench.loadImage(state.current.preview_url),
      ]);
      Object.assign(this.session, {original,result});
      this.draw();
      $("#cleanupStatus").textContent = "红色清除，绿色保护。选好后点击“修补选区”。";
    } catch(error) { $("#cleanupStatus").textContent = error.message; }
    finally { this.setBusy(false); }
  },
  setBusy(active, message) {
    this.busy = active;
    for (const id of ["applyCleanup","undoCleanupSelection","resetCleanupSelection","undoCleanupResult","closeCleanup","closeCleanupTop"]) $("#"+id).disabled = active;
    $("#undoCleanupResult").disabled = active || !state.undo?.length;
    $("#undoCleanupSelection").disabled = active || !this.session?.selectionUndo.length;
    if (message) $("#cleanupStatus").textContent = message;
  },
  draw() {
    const r = this.session;
    $("#undoCleanupSelection").disabled = this.busy || !r?.selectionUndo.length;
    $("#undoCleanupResult").disabled = this.busy || !state.undo?.length;
    if (!r?.result) return;
    const c = $("#cleanupCanvas"), p = state.current.project.canvas;
    const zoom = $("#cleanupZoom").value;
    const scale = zoom === "fit" ? Math.min(1000/p.width, 600/p.height, 1) : Number(zoom);
    c.width = Math.round(p.width*scale); c.height = Math.round(p.height*scale);
    c.style.width = `${c.width}px`; c.style.height = `${c.height}px`;
    const ctx = c.getContext("2d");
    ctx.drawImage($("#cleanupOriginal").checked ? r.original : r.result,0,0,c.width,c.height);
    if (!$("#cleanupMask").checked) return;
    ctx.scale(c.width/p.width,c.height/p.height);
    for (const s of [...r.strokes, ...(r.stroke ? [r.stroke] : [])]) {
      ctx.strokeStyle = ctx.fillStyle = s.mode === "protect" ? "rgba(36,155,111,.55)" : "rgba(226,68,44,.4)";
      const [a,b] = s.points;
      if (s.shape === "rect") ctx.fillRect(Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.abs(b[0]-a[0])+1,Math.abs(b[1]-a[1])+1);
      else {
        ctx.lineWidth = s.radius*2;ctx.lineCap="round";ctx.lineJoin="round";ctx.beginPath();
        s.points.forEach(([x,y],i)=>ctx[i?"lineTo":"moveTo"](x,y));ctx.stroke();
        ctx.beginPath();ctx.arc(a[0],a[1],s.radius,0,Math.PI*2);ctx.fill();
      }
    }
  },
  async apply() {
    if (this.busy || !this.session) return;
    const r = this.session, p = state.current.project;
    const before = state.committed;
    this.setBusy(true,"正在本地修补…");
    try {
      const result = await api(`/api/projects/${p.id}/cleanup`, {base_revision:p.revision,strokes:r.strokes});
      if (before) state.undo.push(before); state.undo=state.undo.slice(-30);state.redo=[];
      state.current=result;state.committed=JSON.stringify(result.project);r.revision=result.project.revision;
      r.selectionUndo=[];renderProject();
      r.result=await workbench.loadImage(result.preview_url);this.draw();
      $("#cleanupStatus").textContent="修补已保存。关闭选区遮罩或勾选“查看原图”检查效果；不满意可撤销修补。";
    } catch(error) { $("#cleanupStatus").textContent=`修补失败：${error.message}。选区已保留，可重试。`; }
    finally { this.setBusy(false); }
  },
  close() {
    if (this.busy) return;
    this.session?.cancelStroke?.();$("#cleanupDialog").close();
  },
};

$("#openCleanup").onclick=()=>cleanupTool.open();
$("#applyCleanup").onclick=()=>cleanupTool.apply();
$("#closeCleanupTop").onclick=$("#closeCleanup").onclick=()=>cleanupTool.close();
$("#cleanupDialog").addEventListener("cancel",event=>{
  if (cleanupTool.busy) event.preventDefault(); else cleanupTool.session?.cancelStroke?.();
});
$("#cleanupZoom").onchange=$("#cleanupMask").onchange=$("#cleanupOriginal").onchange=()=>cleanupTool.draw();
$("#cleanupRadius").oninput=event=>{$("#cleanupRadiusValue").textContent=`${event.target.value} px`;};
$("#undoCleanupSelection").onclick=()=>{
  const r=cleanupTool.session;if(!r || cleanupTool.busy)return;
  if(r.selectionUndo.length)r.strokes=r.selectionUndo.pop();cleanupTool.draw();
};
$("#resetCleanupSelection").onclick=()=>{
  const r=cleanupTool.session;if(!r || cleanupTool.busy)return;
  r.selectionUndo.push(structuredClone(r.strokes));r.strokes=[];cleanupTool.draw();
  $("#cleanupStatus").textContent="选区已清空。点击修补选区可恢复原图。";
};
$("#undoCleanupResult").onclick=async()=>{
  if(cleanupTool.busy || !state.undo.length)return;
  cleanupTool.setBusy(true,"正在撤销修补…");
  try {
    await travelHistory();const p=state.current.project;
    cleanupTool.session.strokes=structuredClone(p.layers[0].style.cleanup_strokes||[]);
    cleanupTool.session.selectionUndo=[];cleanupTool.session.revision=p.revision;
    await cleanupTool.load();
  } finally {cleanupTool.setBusy(false);}
};
$("#cleanupCanvas").addEventListener("pointerdown",event=>{
  const r=cleanupTool.session;
  if(cleanupTool.busy || !r?.result || event.button!==0 || r.stroke)return;
  event.preventDefault();
  const canvas=event.currentTarget,rect=canvas.getBoundingClientRect(),p=state.current.project.canvas;
  const point=e=>[Math.max(0,Math.min(p.width-1,(e.clientX-rect.left)/rect.width*p.width)),Math.max(0,Math.min(p.height-1,(e.clientY-rect.top)/rect.height*p.height))];
  const tool=$("input[name='cleanupBrush']:checked").value, first=point(event);
  r.stroke={mode:tool==="protect"?"protect":"erase",shape:tool==="rect"?"rect":"brush",radius:Number($("#cleanupRadius").value),points:tool==="rect"?[first,first]:[first]};
  canvas.setPointerCapture(event.pointerId);cleanupTool.draw();
  const move=e=>{if(tool==="rect")r.stroke.points[1]=point(e);else if(r.stroke.points.length<2000)r.stroke.points.push(point(e));cleanupTool.draw();};
  const finish=cancel=>{
    if(!r.stroke)return;
    canvas.removeEventListener("pointermove",move);canvas.removeEventListener("pointerup",up);canvas.removeEventListener("pointercancel",stop);
    if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
    const stroke=r.stroke;r.stroke=null;
    if(!cancel){r.selectionUndo.push(structuredClone(r.strokes));r.selectionUndo=r.selectionUndo.slice(-30);r.strokes.push(stroke);}
    cleanupTool.draw();
  };
  const up=()=>finish(false),stop=()=>finish(true);r.cancelStroke=stop;
  canvas.addEventListener("pointermove",move);canvas.addEventListener("pointerup",up);canvas.addEventListener("pointercancel",stop);
});
document.addEventListener("keydown",event=>{
  if(event.key==="Escape" && cleanupTool.session?.stroke){event.preventDefault();cleanupTool.session.cancelStroke();}
});
