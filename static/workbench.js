const main = document.querySelector("#main");
const sidebar = document.querySelector("#sidebar");
const modal = document.querySelector("#modal");
const imageDialog = document.querySelector("#image-dialog");
const names = {character:"角色", scene:"场景", shot:"分镜"};
const sections = {character:"角色库", scene:"场景库", shot:"分镜台"};
const icons = {
  grid:"M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  film:"M3 5h18v14H3z M7 5v14 M17 5v14 M3 10h4 M3 14h4 M17 10h4 M17 14h4",
  character:"M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-2a8 8 0 0 1 16 0v2",
  scene:"M3 4h18v16H3z M3 17l6-6 4 4 3-3 5 6 M16 8h.01",
  shot:"M3 8h18v12H3z M3 8l-1-4 17-3 1 4z M7 3l3 3 M13 2l3 3",
  settings:"M4 6h16 M4 12h16 M4 18h16 M9 3v6 M16 9v6 M8 15v6",
  plus:"M12 5v14 M5 12h14", right:"M4 12h15 M14 6l6 6-6 6", left:"M20 12H5 M10 6l-6 6 6 6",
  folder:"M3 7V5h7l2 2h9v13H3z", monitor:"M3 4h18v13H3z M12 17v4 M8 21h8",
  check:"m4 12 5 5L20 6", close:"m6 6 12 12 M18 6 6 18", download:"M12 3v12 M6 10l6 6 6-6 M4 16v5h16v-5",
  upload:"M12 16V3 M6 9l6-6 6 6 M4 16v5h16v-5", picture:"M3 4h18v16H3z M4 17l6-7 5 6 2-2 4 4",
  spark:"m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3z",
  pen:"m4 16 12-12 4 4L8 20H4z M13 7l4 4", more:"M4 7h16 M4 12h16 M4 17h16",
  copy:"M8 8h13v13H8z M5 17H3V3h14v2", info:"M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
};
const ico = name => `<svg class="svg" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[name]||icons.film}"/></svg>`;
const esc = value => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num = n => String(n).padStart(2,"0");
const button = (act,label,icon="",cls="btn",extra="") => `<button type="button" class="${cls}" data-act="${act}" ${extra}>${icon?ico(icon):""}${esc(label)}</button>`;
const link = (href,label,icon="",cls="btn") => `<a class="${cls}" href="${href}">${icon?ico(icon):""}${esc(label)}</a>`;
const pic = (id,alt="",full=false) => `<img ${full?"":'loading="lazy"'} src="/media/${id}${full?"":"?thumb=1"}" alt="${esc(alt)}">`;
const zoomPic = (id,alt,full=false) => `<button type="button" class="zoom-pic" data-zoom="/media/${id}" aria-label="放大查看${esc(alt)}">${pic(id,alt,full)}</button>`;
const slate = (label,icon="shot") => `<div class="slate">${ico(icon)}<span>${esc(label)}</span></div>`;
const tag = (label,neutral=false) => `<span class="tag ${neutral?"neutral":""}">${esc(label)}</span>`;
const field = (label,html) => `<label class="field"><span>${esc(label)}</span>${html}</label>`;
const input = (name,value="",attrs="") => `<input name="${name}" value="${esc(value)}" ${attrs}>`;
const area = (name,value="",rows=3,placeholder="") => `<textarea name="${name}" rows="${rows}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`;
const opts = (values,current) => values.map(v=>`<option ${v===current?"selected":""} value="${esc(v)}">${esc(v)}</option>`).join("");
const state = {projects:[],project:null,items:[],config:{},views:{},kind:"character",item:null,version:"",view:"",dirty:false};
let token=0, shownHash="", toastTimer, watchTimer;
const object = id=>state.items.find(i=>i.id===id);
const ofKind = k=>state.items.filter(i=>i.kind===k);
const projectUrl = (kind,id)=>`#p/${state.project.id}/${kind}${id?"/"+id:""}`;
const approved = (item,view)=>item.versions.find(v=>v.adopted && (!view || v.view===view));
const threeViews = {"正面全身":"正视图","侧面全身":"侧视图","背面全身":"背视图"};
const defaultView = i=>i.kind==="character" && i.referenceMode==="three"?"正面全身":state.views[i.kind][0];
const referenceModeField = mode=>field("角色参考图模式",`<select name="referenceMode"><option value="single">单视角</option><option value="three" ${mode==="three"?"selected":""}>三视图</option></select>`);
const currentVersion = ()=>state.item?.versions.find(v=>v.id===state.version);
function notify(message) {
  const t=document.querySelector("#toast");t.textContent=message;t.classList.add("show");
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove("show"),4200);
}
async function api(path, method="GET", body) {
  const form=body instanceof FormData;
  const r=await fetch(path,{method,headers:body&&!form?{"Content-Type":"application/json"}:{},
    body:body?(form?body:JSON.stringify(body)):undefined});
  const data=await r.json().catch(()=>({}));
  if(!r.ok){
    const error=Error(data.error||`操作未完成（HTTP ${r.status}）`);
    if(typeof data.draft==="string")error.draft=data.draft;
    error.status=r.status;throw error;
  }
  return data;
}
function report(error) {
  const box=modal.open && modal.querySelector(".notice");
  if(box) box.textContent=error.message;
  else notify(error.message);
}
function openModal(title,body,wide=false) {
  if(modal.open) modal.close();
  modal.classList.toggle("wide",wide);
  modal.innerHTML=`<div class="panelhead"><h2>${esc(title)}</h2>${button("close","", "close","iconbtn",'aria-label="关闭"')}</div>${body}`;
  modal.setAttribute("aria-label",title);
  modal.showModal();
}
function showImage(src,label){
  imageDialog.classList.remove("original");
  imageDialog.querySelector("[data-act=image-size]").setAttribute("aria-pressed","false");
  imageDialog.querySelector("#image-title").textContent=label;
  const img=imageDialog.querySelector("img");
  img.alt=label;img.src=src;
  imageDialog.showModal();
}
imageDialog.addEventListener("close",()=>imageDialog.querySelector("img").removeAttribute("src"));
imageDialog.addEventListener("click",e=>{if(e.target===imageDialog)imageDialog.close();});
function topbar(section) {
  const trail=state.project?`<a href="#library">项目库</a><span>/</span><b>${esc(state.project.name)}</b>`:`<span>工作空间</span><span>/</span><b>项目库</b>`;
  return `<div class="topbar"><div class="crumb">${button("nav","","more","iconbtn burger",'aria-label="展开导航"')}${trail}</div><div class="top-right">${ico("monitor")}<span>本地工作台</span></div></div>`;
}
function nav() {
  const p=state.project;
  const projectLinks=p?`<div class="rail-label">${esc(p.name)}</div><nav class="railnav">${Object.entries(sections).map(([k,v])=>link(projectUrl(k),v,k,"navbtn "+(state.kind===k?"active":""))).join("")}</nav>`:"";
  sidebar.innerHTML=`<a class="brand" href="#library"><img src="/static/mark.svg" alt=""><div><strong>映序</strong><small>FRAMEBOOK</small></div></a>
    <nav class="railnav">${link("#library","项目库","grid","navbtn "+(!p?"active":""))}</nav>${projectLinks}
    <div class="rail-label">最近项目</div><nav class="railnav">${state.projects.slice(0,5).map(p=>`<a class="navbtn recent" href="#p/${p.id}/${p.shots?"shot":"character"}"><i></i><span>${esc(p.name)}</span></a>`).join("")}</nav>
    <div class="rail-bottom">${button("config","设置","settings","navbtn")}<div class="statusline"><i class="status-dot ${state.config.configured?"":"off"}"></i>${state.config.configured?"服务商已配置":"等待配置服务商"}</div></div>`;
  if(!document.querySelector(".mobileveil")) document.body.insertAdjacentHTML("beforeend",'<button class="mobileveil" data-act="nav" aria-label="收起导航"></button>');
}
function projectMenu(p){
  return `<details class="project-menu" name="project-menu"><summary aria-label="${esc(p.name)}的项目操作" title="项目操作">•••</summary><div>
    ${button("rename-project","重命名","pen","menu-action",`data-project="${p.id}"`)}
    ${button("archive-project","归档","folder","menu-action",`data-project="${p.id}"`)}</div></details>`;
}
function createdTime(p){
  const date=new Date(p.created*1000);
  const label=date.toLocaleString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
  return `<time datetime="${date.toISOString()}">创建于${p.created_estimated?"约 ":" "}${esc(label)}</time>`;
}
function library() {
  const recent=localStorage.getItem("framebook.recent");
  const p=state.projects.find(p=>p.id===recent)||state.projects[0];
  const others=state.projects.filter(x=>x!==p);
  const montage=p?[[0,p.covers[0]],[3,p.covers[3]],[1,p.covers[1]]].filter(([n,id],index,all)=>id && all.findIndex(x=>x[1]===id)===index):[];
  main.innerHTML=topbar()+`<div class="wrap"><div class="heading"><div><h1>项目库</h1><p>从角色定稿到逐镜成片，在这里继续你的故事。</p></div><div class="actions">${button("import-project","导入项目","upload")}${button("new-project","创建项目","plus","btn primary")}</div></div>
    ${p?`<article class="feature">${projectMenu(p)}<div class="feature-copy"><div class="eyeline">${ico("folder")} 最近项目</div><h2>${esc(p.name)}</h2><div class="pmeta"><span>${esc(p.ratio)} 画幅</span><span>${p.actors} 位角色</span><span>${p.shots} 个分镜</span></div><div class="pmeta">${createdTime(p)}</div>
      ${link("#p/"+p.id+"/"+(p.shots?"shot":"character"),"进入制作","right","btn accent")}</div><div class="contact">${montage.length?montage.map(([n,id])=>`<figure>${zoomPic(id,p.previews[n]?.picture===id?p.previews[n].name:p.name)}<figcaption>${p.previews[n]?.picture===id?"SHOT "+num(p.previews[n].rank):"REF"}</figcaption></figure>`).join(""):p.style_ref?`<figure>${zoomPic(p.style_ref,"统一画风参考图")}<figcaption>画风参考</figcaption></figure>`:slate("等待首张定稿","film")}</div></article>
    ${others.length?`<div class="sectionhead"><h2>其他项目 <span class="count">${num(others.length)}</span></h2></div><div class="projectgrid">${others.map(x=>`<article class="projecttile">${projectMenu(x)}<a href="#p/${x.id}/${x.shots?"shot":"character"}">${ico("folder")}<strong>${esc(x.name)}</strong><span class="quiet">${x.shots} 个分镜 · ${esc(x.ratio)}</span><span class="quiet">${createdTime(x)}</span></a></article>`).join("")}</div>`:""}
    ${p.previews.length?`<div class="sectionhead"><div><h2>分镜速览</h2><p class="quiet">${esc(p.name)}${p.source?" · 原片参考":""}</p></div>${link("#p/"+p.id+"/shot","全部分镜","right","link")}</div><div class="strip">${p.previews.map(s=>`<div class="strip-item"><div class="still">${s.picture?zoomPic(s.picture,s.name):slate("待生成")}<span class="filmnum">S${num(s.rank)}</span></div><a href="#p/${p.id}/shot/${s.id}"><strong>${esc(s.name)}</strong></a></div>`).join("")}</div>`:""}`
    :emptyBlock("创建第一个故事项目","先建立角色参考，再为每个分镜安排人物和场景。","new-project","创建项目","folder")}</div>`;
}
function emptyBlock(title,detail,act,label,k) {
  return `<div class="empty">${ico(k)}<h3>${esc(title)}</h3><p>${esc(detail)}</p>${button(act,label,"plus","btn primary")}</div>`;
}
function tabs(){
  return `<nav class="workspace-tabs">${Object.entries(sections).map(([k,label])=>`<a class="tab ${state.kind===k?"on":""}" href="${projectUrl(k)}">${ico(k)}${label}<span class="number">${num(ofKind(k).length)}</span></a>`).join("")}</nav>`;
}
function gallery() {
  const k=state.kind,p=state.project,items=ofKind(k);
  const details={character:"确定主要人物的面貌与服装，按镜头需要补充其他视角。",scene:"保存可复用的空间和地标，镜头也可以单独描述新地点。",shot:"按叙事顺序安排画面，为每个镜头单独选择出场角色与场景。"};
  main.innerHTML=topbar()+`<div class="wrap"><div class="heading"><div><div class="subhead quiet">${esc(p.ratio)} 画幅 ${p.source?tag("参考项目"):''}</div><h1>${esc(p.name)}</h1><p>${esc(p.source||"独立故事项目")}</p></div><div class="actions">${button("project-settings","项目设置","settings")}${k==="shot"&&items.length?link("/api/projects/"+p.id+"/export","导出分镜","download"):""}</div></div>${tabs()}
    <div class="sectionhead"><div><h2>${sections[k]} <span class="count">${num(items.length)}</span></h2><p>${details[k]}</p></div>${items.length?button("new-item","新建"+names[k],"plus","btn primary"):""}</div>
    ${items.length?`<div class="cards gallery ${k==="shot"?"shots":""}">${items.map(i=>{const v=approved(i,defaultView(i))||i.versions[0]; const meta=k==="shot"?[...(i.cast||[]).map(a=>object(a.id)?.name),object(i.scene)?.name].filter(Boolean).join(" · "):i.brief;return `<article class="card"><div class="poster">${v?zoomPic(v.id,i.name):slate("等待出图",k)}<span class="filmnum">${k==="shot"?"S":k==="character"?"C":"L"}${num(i.rank)}</span>${v?tag(v.adopted?"已采用":"待选片"):""}</div><a class="card-body" href="${projectUrl(k,i.id)}"><strong>${esc(i.name)}</strong><span class="quiet">${esc(meta||"待填写设定")}</span></a></article>`}).join("")}</div>`:
      emptyBlock("添加第一个"+names[k],details[k],"new-item","新建"+names[k],k)}</div>`;
}
function sceneSelect(i){
  return `<select name="scene"><option value="">本镜单独描述地点</option>${ofKind("scene").map(s=>`<option value="${s.id}" ${s.id===i.scene?"selected":""}>${esc(s.name)}</option>`).join("")}</select>`;
}
function refViews(asset,selected) {
  const base=defaultView(asset);
  const views=[...new Set([base,...asset.versions.filter(v=>v.adopted).map(v=>v.view),selected||base,"仅文字"])];
  return opts(views,selected||base);
}
function actorList(i){
  const actors=ofKind("character");
  return `<fieldset><legend>出场角色</legend><div class="actorlist">${actors.map(a=>{
    const ref=(i.cast||[]).find(x=>x.id===a.id),thumb=approved(a,"脸部近景")||approved(a,defaultView(a))||approved(a,"主图");
    return `<div class="actor"><input id="cast-${a.id}" type="checkbox" name="actor" value="${a.id}" ${ref?"checked":""}>${thumb?zoomPic(thumb.id,a.name):ico("character")}<label for="cast-${a.id}">${esc(a.name)}</label><select aria-label="${esc(a.name)}参考视角" data-actor="${a.id}" ${ref?"":"disabled"}>${refViews(a,ref?.view)}</select></div>`;
  }).join("")||`<span class="quiet">可以制作空镜，也可以先到角色库添加人物。</span>`}</div></fieldset>`;
}
function drawShotReferences(form){
  const rows=[];
  function addAsset(asset,view){
    const version=asset && approved(asset,view);
    if(version)rows.push({id:version.id,label:asset.name+" · "+view});
  }
  addAsset(object(form.elements.scene.value),form.elements.sceneView.value);
  for(const box of form.querySelectorAll("[name=actor]:checked")){
    addAsset(object(box.value),form.querySelector(`[data-actor="${box.value}"]`).value);
  }
  const earlier=new Map(),groups=[];
  for(const shot of ofKind("shot").filter(s=>s.rank<state.item.rank && s.versions.length)){
    const title=`S${num(shot.rank)} · ${shot.name}`;
    const options=shot.versions.map((v,index)=>{
      const label=`V${num(shot.versions.length-index)} · ${v.adopted?"已采用":"候选"}`;
      earlier.set(v.id,{id:v.id,label:title+" · "+label,removable:true});
      return `<option value="${v.id}" ${form.shotRefs.includes(v.id)?"disabled":""}>${esc(title+" · "+label)}</option>`;
    });
    groups.push(`<optgroup label="${esc(title)}">${options.join("")}</optgroup>`);
  }
  const picker=form.elements.earlierVersion;
  picker.innerHTML=`<option value="">${groups.length?"选择一张图片，可继续添加":"暂无可选的前序分镜图片"}</option>`+groups.join("");
  picker.disabled=!groups.length;
  for(const id of form.shotRefs)if(earlier.has(id))rows.push(earlier.get(id));
  state.project.style_refs.forEach((id,index)=>rows.push({id,label:"项目画风参考图 "+(index+1)}));
  const notes=form.querySelector(".shot-ref-notes");
  notes.hidden=!rows.length;
  notes.innerHTML=`<legend>逐图补充说明（可选）</legend>`+rows.map((ref,index)=>`
    <div class="ref-note-row">${zoomPic(ref.id,ref.label)}<div class="ref-note-body"><div class="ref-note-title">
      <label for="note-${ref.id}">图${index+1} · ${esc(ref.label)}</label>
      ${ref.removable?button("remove-shot-ref","移除","","style-remove",`data-picture="${ref.id}" aria-label="移除${esc(ref.label)}"`):""}</div>
      <textarea id="note-${ref.id}" data-note-for="${ref.id}" rows="2" maxlength="12000" placeholder="希望这一镜沿用哪些细节？">${esc(form.imageNotes[ref.id]||"")}</textarea></div></div>`).join("");
}
function editorForm(i){
  return `<section class="panel"><div class="panelhead"><h2>${i.kind==="shot"?"镜头设定":names[i.kind]+"设定"}</h2><span class="quiet" id="dirty-state">已保存</span></div>
    <form id="editor" class="stack pad" data-rev="${i.rev}">${field("名称",input("name",i.name,'required maxlength="100"'))}
    ${field(i.kind==="shot"?"画面与动作":"外观与固定特征",area("brief",i.brief,3,i.kind==="shot"?"谁在什么地方，做了什么？":"写下后续镜头需要保持一致的细节"))}
    ${i.kind==="character"?referenceModeField(i.referenceMode):""}
    ${i.kind==="shot"?`${actorList(i)}<div class="twocol">${field("场景",sceneSelect(i))}${field("场景参考",`<select name="sceneView" ${i.scene?"":"disabled"}>${i.scene?refViews(object(i.scene),i.sceneView):opts(["主图"],"主图")}</select>`)}</div>
      ${field("前序分镜参考图",'<select name="earlierVersion"></select>')}<fieldset class="shot-ref-notes"></fieldset>
      ${field("机位与构图",area("camera",i.camera,2,"景别、视线方向、前后景关系"))}
      <details ${i.voice?"open":""}><summary>旁白 / 字幕</summary>${field("随分镜清单导出",area("voice",i.voice,2))}</details>`:""}
    <div class="formfoot"><button class="btn" type="submit">保存设定</button></div></form></section>`;
}
function generationBar(i) {
  const pending=i.job?.state==="running";
  return `<section class="panel"><div class="panelhead"><h3>${i.kind==="shot"?"生成分镜":"生成参考图"}</h3>${tag(state.project.ratio,true)}</div><form id="generate" class="stack pad">
    ${i.kind!=="shot"?'<div id="view-controls"></div>':""}
    <details ${i.viewNotes?.[state.view]?"open":""}><summary>补充要求与参考图（可选）</summary><div class="stack lined">${field("本次补充要求",area("direction",i.viewNotes?.[state.view],2))}${field("上传参考图",'<input type="file" name="reference" accept="image/png,image/jpeg,image/webp">')}
      <div class="upload-note" hidden>${field("上传参考图的补充说明",area("uploadNote","",2))}</div></div></details>
    <div class="notice" id="job-message">${esc(pending?"已向服务商提交，返回后可在这里选片。":i.job?.state==="error"?i.job.error:"")}</div>
    <button type="submit" class="btn primary" ${pending?"disabled":""}>${ico("spark")}${pending?"正在生成…":"生成一张候选图"}</button></form></section>`;
}
function drawGenerationViews(){
  const box=document.querySelector("#view-controls");
  if(!box)return;
  const item=state.item,mode=document.querySelector('#editor [name="referenceMode"]');
  if(item.viewNotes && box.dataset.view!==state.view){
    const field=box.closest("form").elements.direction;
    box.drafts ??= {...item.viewNotes};
    if(box.dataset.view)box.drafts[box.dataset.view]=field.value;
    field.value=box.drafts[state.view]||"";
    if(field.value)field.closest("details").open=true;
    box.dataset.view=state.view;
  }
  const pending=item.job?.state==="running";
  if(mode)mode.disabled=pending;
  const three=mode?.value==="three";
  const focused=box.contains(document.activeElement);
  if(three){
    const front=approved(item,"正面全身");
    const options=Object.entries(threeViews).map(([view,label])=>{
      const version=approved(item,view)||item.versions.find(v=>v.view===view);
      const locked=view!=="正面全身" && !front;
      let status=version?(version.adopted?"已采用":"待选片"):"待生成";
      if(locked)status="待采用正视图";
      else if(pending && item.job.view===view)status="生成中";
      else if(view!=="正面全身" && version?.meta.frontVersion && version.meta.frontVersion!==front.id)status="来自旧正视图";
      return `<label><input type="radio" name="view" aria-label="${label}" value="${view}" ${view===state.view?"checked":""} ${locked?"disabled":""}>
        <span class="angle-card">${version?pic(version.id):ico("character")}<strong>${label}</strong><small>${status}</small></span></label>`;
    }).join("");
    box.innerHTML=`<fieldset><legend>本次视角</legend><div class="angle-grid">${options}</div></fieldset>
      <p class="inline-note">${front?"侧视图和背视图均以已采用的正视图为参考。":"先生成正视图，满意后点击「采用此版」，即可生成侧视图和背视图。"}</p>
      ${front?`<input type="hidden" name="frontVersion" value="${front.id}">`:""}`;
  }else{
    box.innerHTML=field("本次视角",`<select name="view">${opts(state.views[item.kind],state.view)}</select>`);
  }
  if(focused)box.querySelector("input:checked,select")?.focus({preventScroll:true});
  if(!pending){
    const submit=box.closest("form").querySelector("button[type=submit]");
    submit.innerHTML=ico("spark")+(three?`生成${threeViews[state.view]}候选`:"生成一张候选图");
  }
}
function preview(i){
  const v=currentVersion(), versions=i.versions.filter(v=>v.view===state.view);
  const pending=i.job?.state==="running" && i.job.view===state.view;
  const pendingCard=pending?`<div class="vthumb pending-version" role="status">
    <span class="pending-art" aria-hidden="true"><span class="pending-spinner"></span></span>V${num(versions.length+1)} · 生成中</div>`:"";
  return `<section id="preview" class="panel"><div class="panelhead"><h2>${esc(state.view)}预览</h2>${v?tag(v.adopted?"已采用":"候选图",!v.adopted):tag(pending?"生成中":"待生成",true)}</div>
    <div class="viewer">${v?zoomPic(v.id,i.name+" · "+v.view,true):slate(pending?"正在生成此视角的第一张图":"生成或导入此视角的第一张图",i.kind)}</div>
    <div class="preview-actions">${v?`${button("adopt",v.adopted?"已采用":"采用此版","check","btn sm",v.adopted?"disabled":"")}${button("repair","局部返修","pen","btn sm")}${button("details","版本信息","info","btn sm")}`:""}
    <label class="btn sm filebtn">${ico("upload")}导入现有图<input id="import-file" type="file" aria-label="导入现有图" accept="image/png,image/jpeg,image/webp"></label></div>
    ${pending||versions.length?`<div class="history">${pendingCard}${versions.map((v,index)=>`<button class="vthumb ${v.id===state.version?"sel":""}" data-version="${v.id}">${pic(v.id,"")}V${num(versions.length-index)} · ${v.adopted?"定稿":"候选"}</button>`).join("")}</div>`:""}</section>`;
}
function editor(){
  const i=state.item,k=i.kind,items=ofKind(k);
  main.innerHTML=topbar()+`<div class="wrap"><div class="subhead"><a class="quiet" href="${projectUrl(k)}">${ico("left")}返回${sections[k]}</a></div>
    <div class="heading"><div><div class="eyeline quiet">${names[k]} ${num(i.rank)} / ${num(items.length)}</div><h1>${esc(i.name)}</h1></div>
    <div class="actions">${k==="shot"?`${button("prev-order","前移","","btn sm",i.rank===1?"disabled":"")}${button("next-order","后移","","btn sm",i.rank===items.length?"disabled":"")}`:""}${button("new-item","新建"+names[k],"plus","btn")}${button("generate-shortcut",i.job?.state==="running"?"正在生成…":"生成候选图","spark","btn primary",i.job?.state==="running"?"disabled":"")}</div></div>
    ${k==="shot"?`<nav class="shotnav" aria-label="镜头顺序">${items.map(s=>`<a href="${projectUrl(k,s.id)}" class="${s.id===i.id?"on":""}" title="${esc(s.name)}">S${num(s.rank)}</a>`).join("")}</nav>`:""}
    <div class="editlayout"><div>${preview(i)}</div><div class="stack">${editorForm(i)}${generationBar(i)}</div></div></div>`;
  drawGenerationViews();
  if(k==="shot"){
    const form=document.querySelector("#editor");
    form.shotRefs=[...(i.shotRefs||[])];form.imageNotes={...i.imageNotes};
    drawShotReferences(form);
  }
  if(i.job?.state==="running") monitor(i.job.id,i.id);
}
function selectView(view,vid) {
  state.view=view;
  const list=state.item.versions.filter(v=>v.view===view);
  state.version=vid||list.find(v=>v.adopted)?.id||list[0]?.id||"";
}
async function route(){
  const nextHash=location.hash||"#library";
  if(state.dirty && shownHash && nextHash!==shownHash && !confirm("有未保存的设定，离开此页？")){
    history.replaceState(null,"",shownHash);return;
  }
  const key=++token;clearTimeout(watchTimer);
  try{
    const [list,config]=await Promise.all([api("/api/projects"),api("/api/config")]);
    if(list.api_version!==6)throw Error("页面和本机服务的版本不匹配，请关闭旧版映序服务后重新启动。");
    const parts=nextHash.slice(1).split("/");
    const data=parts[0]==="p"&&parts[1]?await api("/api/projects/"+encodeURIComponent(parts[1])):null;
    if(key!==token) return;
    state.projects=list.projects;state.archived=list.archived;state.views=list.views;state.defaultStyle=list.style;state.config=config;
    state.project=data?.project;state.items=data?.items||[];state.kind=Object.hasOwn(names,parts[2])?parts[2]:"character";
    state.item=parts[3]?object(parts[3]):null;state.dirty=false;shownHash=nextHash;
    document.body.classList.remove("navopen");nav();
    if(!state.project) library();
    else{
      localStorage.setItem("framebook.recent",state.project.id);
      if(state.item && state.item.kind===state.kind){
        const job=state.item.job;
        selectView(job?.state==="running"?job.view:defaultView(state.item));editor();
      }
      else gallery();
    }
    window.scrollTo(0,0);
  }catch(e){main.innerHTML=`<div class="wrap"><h2>暂时无法打开工作区</h2><p>${esc(e.message)}</p>${link("#library","返回项目库")}</div>`;}
}
window.addEventListener("hashchange",route);
window.addEventListener("beforeunload",e=>{if(state.dirty){e.preventDefault();e.returnValue="";}});
function importDialog(draft={}){
  openModal("导入项目",`<form id="import-form" class="stack pad">
    ${field("文本类型",'<select name="mode"><option value="draft">映序导入稿（本机解析）</option><option value="story">故事原文（文本模型拆镜）</option></select>')}
    <p class="inline-note import-help"></p>
    ${field("读取文本文件",'<input type="file" name="storyFile" accept=".json,.txt,.md,text/plain,application/json,text/markdown"><small>支持 UTF-8 编码的 JSON、TXT、Markdown，最大 4 MiB。</small>')}
    ${field("文本内容",'<textarea name="text" rows="9" required spellcheck="false" placeholder="在这里粘贴内容，也可以读取文件后继续编辑。"></textarea>')}
    <p class="inline-note">创建后可编辑角色、场景和分镜。准备好时，在对应条目中点击生成候选图。</p>
    <div class="notice" role="status"></div><div class="formfoot">${button("close","取消")}<button type="submit" class="btn primary">解析并创建项目</button></div></form>`,true);
  const form=modal.querySelector("#import-form");
  form.requestId=draft.requestId||crypto.randomUUID();
  form.elements.mode.value=draft.mode||"draft";
  form.elements.text.value=draft.text||"";
  importHelp(form);
}
function importHelp(form){
  const story=form.elements.mode.value==="story";
  const help=form.querySelector(".import-help");
  if(story){
    help.textContent=state.config.textModel?`原文将发送到已配置的文本模型 ${state.config.textModel}，用于提取角色、场景和分镜。`:"先在设置中填写用于解析故事的文本模型 ID。";
    if(!state.config.textModel)help.insertAdjacentHTML("beforeend"," "+button("import-config","打开设置","","btn sm"));
  }else{
    help.innerHTML='请让 framebook-story-prompts 输出「映序导入稿」，然后将结果粘贴到下方。 <a class="link" href="/static/映序导入示例.json" download>下载示例</a>';
  }
  form.querySelector("button[type=submit]").disabled=Boolean(form.reading || (story && !state.config.textModel));
}
async function readStoryFile(input){
  const form=input.form,file=input.files[0];
  if(!file)return;
  form.reading=file;
  form.elements.text.readOnly=true;
  form.querySelector(".notice").textContent="正在读取文件…";
  importHelp(form);
  try{
    if(file.size>4*1024*1024)throw Error("文件超过 4 MiB，请缩短后重试。");
    const contents=new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer());
    if(form.reading===file){
      form.elements.text.value=contents;
      form.querySelector(".notice").textContent="";
    }
  }catch(error){
    if(form.reading===file)form.querySelector(".notice").textContent=error instanceof TypeError?"无法按 UTF-8 读取文件，请检查文件编码。":error.message;
  }finally{
    if(form.reading===file){
      form.reading=null;
      form.elements.text.readOnly=false;
      input.value="";
      importHelp(form);
    }
  }
}
function projectDialog(create){
  const p=create?{name:"",ratio:"3:4",style:state.defaultStyle}:state.project;
  openModal(create?"创建故事项目":"项目设置",`<form id="project-form" class="stack pad" data-new="${create}">
    ${field("项目名称",input("name",p.name,'required maxlength="80" autofocus placeholder="给故事起个名字"'))}
    ${field("统一画幅",`<select name="ratio">${opts(["3:4","9:16","16:9","1:1"],p.ratio)}</select>`)}
    ${field("统一画风",area("style",p.style,4,"光线、色彩、材质与摄影风格"))}
    ${field("画风参考图（可多选）",'<input type="file" name="style_image" multiple accept="image/png,image/jpeg,image/webp"><small>可以分批添加，生成时按显示顺序引用。</small>')}
    <div class="style-preview"></div>
    ${p.folder?`<small class="quiet media-location">图片目录 · media/${esc(p.folder)}/</small>`:""}
    <div class="notice"></div><div class="formfoot">${button("close","取消")}<button class="btn primary">${create?"创建并进入角色库":"保存项目"}</button></div></form>`);
  const form=modal.querySelector("#project-form");
  form.pictures=(p.style_refs||[]).map(id=>({id}));
  drawStylePreviews(form);
}
function drawStylePreviews(form){
  form.querySelector(".style-preview").innerHTML=form.pictures.map((p,index)=>{
    const label="画风参考图 "+(index+1),src=p.url||"/media/"+p.id;
    return `<div><button type="button" class="zoom-pic" data-zoom="${esc(src)}" aria-label="放大查看${label}"><img src="${esc(p.url||src+"?thumb=1")}" alt="${label}"></button>
      ${button("remove-style","移除","","style-remove",`data-index="${index}" aria-label="移除${label}"`)}</div>`;
  }).join("");
}
modal.addEventListener("close",()=>{
  if(!modal.open)for(const p of modal.querySelector("#project-form")?.pictures||[])if(p.url)URL.revokeObjectURL(p.url);
});
function newItemDialog(){
  const k=state.kind,last=k==="shot"?(state.item||ofKind("shot").at(-1)):null;
  openModal("新建"+names[k],`<form id="item-form" class="stack pad" data-previous="${last?.id||""}">
    ${field(names[k]+"名称",input("name","",'required autofocus maxlength="100"'))}
    ${field(k==="shot"?"画面与动作":"外观与固定特征",area("brief","",3,"可以稍后补充"))}
    ${k==="character"?referenceModeField("single"):""}
    ${last?`<label><input type="checkbox" name="inherit"> 沿用「${esc(last.name)}」的角色与场景</label>`:""}
    <div class="notice"></div><div class="formfoot">${button("close","取消")}<button class="btn primary">创建${names[k]}</button></div></form>`);
}
function configDialog(){
  const c=state.config;
  openModal("设置",`<div class="stack pad"><section><h3>已归档项目</h3><div class="archive-list"></div></section>
    <section class="lined"><h3>API 设置</h3><form id="config-form" class="stack">${field("服务地址",input("base",c.base,'required type="url" placeholder="https://api.example.com/v1"'))}
    ${field("生图模型 ID",input("model",c.model,'required'))}${field("文本模型 ID（故事原文拆镜）",input("textModel",c.textModel,'maxlength="160" placeholder="选填，使用同一服务的对话模型"'))}
    ${field("API Key",input("key","",`type="password" autocomplete="new-password" placeholder="${c.configured?"已保存，留空沿用当前密钥":"输入服务商密钥"}"`))}
    <p class="inline-note">所有项目共用此配置。密钥由当前 Windows 账户加密保存在本机。</p><div class="notice"></div>
    <div class="formfoot">${button("close","取消")}<button class="btn primary">保存配置</button></div></form></section></div>`);
  drawArchives();
}
function drawArchives(){
  modal.querySelector(".archive-list").innerHTML=state.archived.map(p=>`<div><strong>${esc(p.name)}</strong>
    ${button("restore-project","恢复","","btn sm",`data-project="${p.id}"`)}
    ${button("purge-project","彻底删除","","btn sm danger",`data-project="${p.id}"`)}</div>`).join("")||'<p class="quiet">暂无归档项目</p>';
}
async function refreshLibrary(){
  const list=await api("/api/projects");
  state.projects=list.projects;state.archived=list.archived;nav();
  if(!state.project)library();
  if(modal.open && modal.querySelector(".archive-list"))drawArchives();
}
async function refreshItem() {
  const active=state.item?.id;
  if(!active) return false;
  const data=await api("/api/projects/"+state.project.id);
  if(state.item?.id!==active) return false;
  state.items=data.items;state.item=object(active);state.project=data.project;return true;
}
function drawPreview(){
  document.querySelector("#preview")?.replaceWith(fragment(preview(state.item)));
  drawGenerationViews();
}
function fragment(html){const t=document.createElement("template");t.innerHTML=html.trim();return t.content.firstElementChild;}
async function saveEditor(){
  if(!state.dirty) return;
  const form=document.querySelector("#editor");
  if(!form.reportValidity()) throw Error("请补全镜头或素材名称。");
  const data=Object.fromEntries(new FormData(form));
  const item=state.item;
  if(item.kind==="shot"){
    data.cast=[...form.querySelectorAll("[name=actor]:checked")].map(a=>({id:a.value,view:form.querySelector(`[data-actor="${a.value}"]`).value}));
    data.shotRefs=form.shotRefs;
    data.imageNotes=Object.fromEntries([...form.querySelectorAll("[data-note-for]")].filter(el=>el.value.trim()).map(el=>[el.dataset.noteFor,el.value.trim()]));
  }
  data.rev=Number(form.dataset.rev);
  const saved=await api("/api/items/"+item.id,"PATCH",data);
  if(state.item?.id!==item.id) return;
  Object.assign(state.item,saved.fields,{rev:data.rev+1});form.dataset.rev=String(state.item.rev);
  state.dirty=false;document.querySelector("#dirty-state").textContent="已保存";
  document.querySelector(".heading h1").textContent=data.name;
}
function markDirty(){
  state.dirty=true;const tag=document.querySelector("#dirty-state");if(tag) tag.textContent="有未保存修改";
}
document.addEventListener("input",e=>{
  if(e.target.closest("#editor")){
    if(e.target.dataset.noteFor)e.target.form.imageNotes[e.target.dataset.noteFor]=e.target.value;
    markDirty();
  }
});
document.addEventListener("change",async e=>{
  const el=e.target;
  try{
    if(el.form?.id==="import-form"){
      if(el.name==="mode"){
        const f=el.form;
        if(f.modeTexts){
          f.modeTexts[el.value==="draft"?"story":"draft"]=f.elements.text.value;
          f.elements.text.value=f.modeTexts[el.value];
        }
        f.querySelector(".notice").textContent="";importHelp(f);
      }
      if(el.name==="storyFile")await readStoryFile(el);
    }
    if(el.name==="style_image"){
      for(const file of el.files)el.form.pictures.push({file,url:URL.createObjectURL(file)});
      el.value="";drawStylePreviews(el.form);
    }
    if(el.closest("#editor")){
      markDirty();
      if(el.name==="referenceMode"){selectView(el.value==="three"?"正面全身":"主图");drawPreview();}
      if(el.name==="actor") document.querySelector(`[data-actor="${el.value}"]`).disabled=!el.checked;
      if(el.name==="scene"){
        const s=object(el.value),select=document.querySelector('[name="sceneView"]');
        select.disabled=!s;select.innerHTML=s?refViews(s,approved(s,"主图")?"主图":"仅文字"):opts(["主图"],"主图");
      }
      if(el.name==="earlierVersion" && el.value)el.form.shotRefs.push(el.value);
      if(["actor","scene","sceneView","earlierVersion"].includes(el.name)||el.hasAttribute("data-actor"))drawShotReferences(el.form);
    }
    if(el.name==="reference" && el.form?.id==="generate"){
      el.form.querySelector(".upload-note").hidden=!el.files.length;el.form.elements.uploadNote.value="";
    }
    if(el.closest("#generate") && el.name==="view"){selectView(el.value);drawPreview();}
    if(el.id==="import-file"&&el.files[0]){
      const iid=state.item.id,view=state.view,form=new FormData();
      form.set("file",el.files[0]);form.set("view",view);
      const res=await api("/api/items/"+iid+"/upload","POST",form);
      if(state.item?.id===iid && await refreshItem()){selectView(view,res.id);drawPreview();notify("图片已导入，可以采用此版。");}
    }
  }catch(err){report(err);el.value=el.type==="file"?"":el.value;}
});
function monitor(jid,iid) {
  clearTimeout(watchTimer);
  async function tick() {
    if(state.item?.id!==iid)return;
    let j;
    try{j=await api("/api/jobs/"+jid);}catch(error){
      if(state.item?.id!==iid)return;
      if(error.status===404){notify("此项目已删除。");location.hash="#library";return;}
      watchTimer=setTimeout(tick,2400);return;
    }
    if(state.item?.id!==iid)return;
    if(j.state==="running"){watchTimer=setTimeout(tick,1400);return;}
    if(!await refreshItem())return;
    if(j.state==="done"){
      if(state.view===j.view)selectView(j.view,j.version);
      notify(`${j.view}候选图已保存，请选片。`);
    }
    if(document.querySelector("#generate")){
      document.querySelector("#generate").closest("section").replaceWith(fragment(generationBar(state.item)));
      drawPreview();
      const shortcut=document.querySelector('[data-act="generate-shortcut"]');
      if(shortcut){shortcut.disabled=false;shortcut.innerHTML=ico("spark")+"生成候选图";}
    }
  }
  watchTimer=setTimeout(()=>tick().catch(report),900);
}
async function generate(body){
  const iid=state.item.id,view=body.get("view")||defaultView(state.item);
  body.set("request",crypto.randomUUID());body.set("rev",String(state.item.rev));
  const res=await api("/api/items/"+iid+"/generate","POST",body);
  if(state.item?.id!==iid)return;
  state.item.job={state:"running",id:res.id,view};
  drawPreview();
  const btn=document.querySelector("#generate button[type=submit]");btn.disabled=true;btn.textContent="正在生成…";
  const shortcut=document.querySelector('[data-act="generate-shortcut"]');shortcut.disabled=true;shortcut.textContent="正在生成…";
  document.querySelector("#job-message").textContent="已提交，可切换到其他镜头。";
  monitor(res.id,iid);
}
function versionDetails(){
  const v=currentVersion(),m=v.meta;
  const refs=(m.refs||[]).map(id=>{
    if(id===m.style_ref || m.style_refs?.includes(id))return "项目画风参考图"+(m.style_refs?.length>1?" "+(m.style_refs.indexOf(id)+1):"");
    const item=state.items.find(i=>i.versions.some(v=>v.id===id));
    return item?`${item.name} · ${item.versions.find(v=>v.id===id).view}`:"历史版本";
  });
  openModal("版本信息",`<div class="stack pad"><div class="tags">${tag(v.origin)}${tag(v.view,true)}</div>
    <p class="quiet">${esc(new Date(v.created*1000).toLocaleString())}${m.model?" · "+esc(m.model):""}</p>
    ${m.refDetails?.length?`<div><h3>本次实际使用的参考</h3><ol class="ref-records">${m.refDetails.map(r=>`<li>${esc(r.label)}${r.note?`<p>${esc(r.note)}</p>`:""}</li>`).join("")}</ol></div>`:refs.length?field("本次实际使用的参考",`<span>${esc(refs.join("、"))}</span>`):""}
    ${m.source?field("原始文件",`<span>${esc(m.source)}</span>`):""}${m.prompt?field("提交给模型的画面指令",`<div class="doc">${esc(m.prompt)}</div>`):""}
    <a class="btn" download="${esc(state.item.name)}" href="/media/${v.id}">${ico("download")}下载此图</a></div>`);
}
async function repairDialog(){
  const v=currentVersion();
  openModal("局部返修",`<form id="repair-form" data-parent="${v.id}" class="stack pad">
    <p class="inline-note">直接填写修改要求即可按整图返修。也可以涂选区域，只修改选区。结果另存为候选图。</p>
    <div class="maskstage">${pic(v.id,"待修底图",true)}<canvas aria-label="用画笔涂出修改区域（可选）"></canvas></div>
    <div class="row">${field("画笔大小",'<input type="range" name="brush" min="1" max="18" value="5">')}${button("clear-mask","清空遮罩","","btn sm")}</div>
    ${field("修改要求",area("direction","",2,"例如：把外套改成深蓝色，保留胸前的徽章"))}
    <div class="notice"></div><div class="formfoot">${button("close","取消")}<button class="btn primary">生成一张返修候选</button></div></form>`,true);
  const img=modal.querySelector(".maskstage img"),c=modal.querySelector("canvas");await img.decode();
  c.width=img.naturalWidth;c.height=img.naturalHeight;
  const ctx=c.getContext("2d");ctx.lineCap="round";ctx.lineJoin="round";ctx.strokeStyle="#bb96ff";ctx.fillStyle="#bb96ff";
  c.style.opacity=".65";
  let last=null;
  const point=e=>{const b=c.getBoundingClientRect();return [(e.clientX-b.left)*c.width/b.width,(e.clientY-b.top)*c.height/b.height];};
  const paint=e=>{
    const pt=point(e),size=Math.max(2,c.width*Number(modal.querySelector("[name=brush]").value)/100);
    ctx.lineWidth=size;ctx.beginPath();ctx.moveTo(...(last||pt));ctx.lineTo(...pt);ctx.stroke();
    ctx.beginPath();ctx.arc(...pt,size/2,0,Math.PI*2);ctx.fill();last=pt;c.dataset.used="1";
  };
  c.onpointerdown=e=>{c.setPointerCapture(e.pointerId);last=null;paint(e);};
  c.onpointermove=e=>{if(c.hasPointerCapture(e.pointerId))paint(e);};
  c.onpointerup=c.onpointercancel=e=>{if(c.hasPointerCapture(e.pointerId))c.releasePointerCapture(e.pointerId);last=null;};
}
async function submit(form){
  const data=Object.fromEntries(new FormData(form));
  if(form.id==="config-form"){
    state.config=await api("/api/config","POST",data);nav();modal.close();notify("共享配置已保存。");
    if(form.importDraft)importDialog(form.importDraft);
  }else if(form.id==="import-form"){
    if(form.reading)return;
    const notice=form.querySelector(".notice");
    notice.textContent=data.mode==="story"?"文本模型正在拆镜，完成后将创建项目…":"正在校验导入稿…";
    const controls=[...form.elements];
    for(const control of controls)control.disabled=true;
    try{
      const {id,counts}=await api("/api/projects/import","POST",{mode:data.mode,text:data.text,request:form.requestId});
      modal.close();
      location.hash="#p/"+id+"/"+(counts.shot?"shot":counts.character?"character":counts.scene?"scene":"character");
      notify(`已创建项目，含 ${counts.character||0} 位角色、${counts.scene||0} 个场景、${counts.shot||0} 个分镜。`);
    }catch(error){
      if(error.draft){
        form.modeTexts={story:data.text,draft:error.draft};
        form.elements.mode.value="draft";
        form.elements.text.value=error.draft;
        importHelp(form);
      }
      throw error;
    }finally{
      for(const control of controls)control.disabled=false;
    }
  }else if(form.id==="project-form"){
    const create=form.dataset.new==="true";
    const body=new FormData(form);
    body.delete("style_image");
    for(const p of form.pictures)if(p.file)body.append("style_image",p.file);
    if(!create){
      body.set("rev",state.project.rev);
      for(const id of state.project.style_refs)if(!form.pictures.some(p=>p.id===id))body.append("remove_style",id);
    }
    const p=await api(create?"/api/projects":"/api/projects/"+state.project.id,create?"POST":"PATCH",body);
    modal.close();if(create)location.hash="#p/"+p.id+"/character";else await route();
  }else if(form.id==="rename-form"){
    await api("/api/projects/"+form.dataset.project+"/manage","POST",{action:"rename",rev:Number(form.dataset.rev),name:data.name});
    modal.close();await refreshLibrary();
  }else if(form.id==="item-form"){
    const prev=object(form.dataset.previous);
    if(data.inherit && prev)Object.assign(data,{cast:prev.cast,scene:prev.scene,sceneView:prev.sceneView});
    data.kind=state.kind;const res=await api("/api/projects/"+state.project.id+"/items","POST",data);
    modal.close();location.hash=projectUrl(state.kind,res.id);
  }else if(form.id==="editor"){
    await saveEditor();notify("设定已保存。");
  }else if(form.id==="generate"){
    const body=new FormData(form);if(!body.get("reference")?.size)body.delete("reference");
    await saveEditor();await generate(body);
  }else if(form.id==="repair-form"){
    if(!data.direction.trim())throw Error("请填写修改要求。");
    const body=new FormData(),c=form.querySelector("canvas");
    if(c.dataset.used){
      const out=document.createElement("canvas");out.width=c.width;out.height=c.height;const ctx=out.getContext("2d");
      ctx.drawImage(c,0,0);ctx.globalCompositeOperation="source-in";ctx.fillStyle="white";ctx.fillRect(0,0,c.width,c.height);
      ctx.globalCompositeOperation="destination-over";ctx.fillStyle="black";ctx.fillRect(0,0,c.width,c.height);
      body.set("mask",await new Promise(resolve=>out.toBlob(resolve)),"mask.png");
    }
    body.set("direction",data.direction);body.set("parent",form.dataset.parent);body.set("view",state.view);
    await saveEditor();await generate(body);modal.close();
  }
}
document.addEventListener("submit",async e=>{
  e.preventDefault();const f=e.target,b=e.submitter||f.querySelector("[type=submit]");if(f.dataset.busy)return;
  f.dataset.busy="1";if(b)b.disabled=true;
  try{await submit(f);}catch(err){report(err);}
  finally{delete f.dataset.busy;if(b?.isConnected)b.disabled=f.id==="generate" && state.item?.job?.state==="running";}
});
modal.addEventListener("cancel",e=>{if(modal.querySelector("#import-form")?.dataset.busy)e.preventDefault();});
document.addEventListener("click",async e=>{
  const menu=document.querySelector(".project-menu[open]");
  if(menu && !menu.contains(e.target))menu.open=false;
  const t=e.target.closest("[data-act],[data-version],[data-zoom]");if(!t||t.disabled)return;
  const act=t.dataset.act;
  try{
    if(t.dataset.zoom){showImage(t.dataset.zoom,t.querySelector("img").alt);return;}
    if(t.dataset.version){
      state.version=t.dataset.version;drawPreview();
      if(e.target.tagName==="IMG")document.querySelector(".viewer .zoom-pic").click();
      return;
    }
    if(act==="close-image")imageDialog.close();
    if(act==="image-size")t.setAttribute("aria-pressed",String(imageDialog.classList.toggle("original")));
    if(act==="nav")document.body.classList.toggle("navopen");
    if(act==="close" && !modal.querySelector("#import-form")?.dataset.busy)modal.close();
    if(act==="import-project")importDialog();
    if(act==="import-config"){
      const f=t.form,draft={mode:f.elements.mode.value,text:f.elements.text.value,requestId:f.requestId};
      configDialog();modal.querySelector("#config-form").importDraft=draft;
    }
    if(act==="config"){await refreshLibrary();configDialog();}
    if(act==="new-project")projectDialog(true);
    if(act==="project-settings")projectDialog(false);
    if(act==="remove-shot-ref"){
      const form=t.form;
      form.shotRefs=form.shotRefs.filter(id=>id!==t.dataset.picture);
      drawShotReferences(form);markDirty();form.elements.earlierVersion.focus();
    }
    if(act==="remove-style"){
      const form=t.form,[p]=form.pictures.splice(Number(t.dataset.index),1);
      if(p.url)URL.revokeObjectURL(p.url);
      drawStylePreviews(form);
    }
    if(act==="rename-project"){
      const p=state.projects.find(p=>p.id===t.dataset.project);
      t.closest("details").open=false;
      openModal("重命名项目",`<form id="rename-form" class="stack pad" data-project="${p.id}" data-rev="${p.rev}">
        ${field("项目名称",input("name",p.name,'required maxlength="80" autofocus'))}<div class="notice"></div>
        <div class="formfoot">${button("close","取消")}<button class="btn primary">保存名称</button></div></form>`);
    }
    if(act==="archive-project" || act==="restore-project" || act==="purge-project"){
      const archive=act==="archive-project", purge=act==="purge-project";
      const p=(archive?state.projects:state.archived).find(p=>p.id===t.dataset.project);
      if(purge && !confirm(`彻底删除「${p.name}」及其中所有图片、角色、场景和分镜？此操作无法撤销。`))return;
      t.disabled=true;
      if(purge)await api("/api/projects/"+p.id,"DELETE");
      else await api("/api/projects/"+p.id+"/manage","POST",{action:archive?"archive":"restore",rev:p.rev});
      if(archive && localStorage.getItem("framebook.recent")===p.id)localStorage.removeItem("framebook.recent");
      await refreshLibrary();notify(archive?"项目已归档，可在设置中恢复。":purge?"项目已彻底删除。":"项目已恢复。");
    }
    if(act==="new-item"){if(state.item)await saveEditor();newItemDialog();}
    if(act==="details")versionDetails();
    if(act==="generate-shortcut")document.querySelector("#generate").requestSubmit();
    if(act==="adopt"){
      t.disabled=true;const v=currentVersion(),iid=state.item.id;await api("/api/versions/"+v.id+"/adopt","POST",{});
      if(state.item?.id===iid && await refreshItem()){drawPreview();notify(state.item.kind==="shot"?"已采用，导出时将使用此版。":"已采用，可在分镜中引用此版本。");}
    }
    if(act==="prev-order"||act==="next-order"){
      await saveEditor();await api("/api/items/"+state.item.id+"/move","POST",{delta:act==="prev-order"?-1:1});await route();
    }
    if(act==="repair")await repairDialog();
    if(act==="clear-mask"){const c=modal.querySelector("canvas");c.getContext("2d").clearRect(0,0,c.width,c.height);delete c.dataset.used;}
  }catch(err){t.disabled=false;report(err);}
});
route();
