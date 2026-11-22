const { chromium } = require(process.env.PLAYWRIGHT_CORE || "playwright-core");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { createInterface } = require("node:readline");
const { join } = require("node:path");
const { readFileSync } = require("node:fs");
const assert = require("node:assert/strict");
const fixture = `
import json, logging, tempfile, threading
from pathlib import Path
from PIL import Image
from werkzeug.serving import make_server
from storage import initialize
from app import create_app
from credentials import save_config
import imaging
import app as workbench
def split_story(text, config):
    assert config["textModel"] == "story-test"
    if text == "需要校对的原文":
        return "{}"
    draft = json.loads((workbench.HERE/"static/映序导入示例.json").read_text(encoding="utf-8"))
    draft["project"]["name"] = text
    return json.dumps(draft, ensure_ascii=False)
workbench.split_story = split_story
release_image=threading.Event()
def fake(config, prompt, images):
    if "等待测试放行" in prompt and not release_image.wait(30):
        raise ValueError("测试超时")
    if "测试生成失败" in prompt:
        raise ValueError("测试生成失败")
    return Image.new("RGB", (96,128), (100,120,150))
imaging.request_image = fake
logging.getLogger("werkzeug").setLevel(logging.ERROR)
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    initialize(root)
    save_config(root/"service.dpapi", {"base":"https://fake.invalid/v1","model":"test","key":"test-only"})
    app=create_app(root)
    @app.post("/test/release-image")
    def release():
        release_image.set()
        return "",204
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(server.server_port, flush=True)
    input()
    server.shutdown()
`;
(async()=>{
  const server=spawn("python",["-u","-c",fixture],{cwd:__dirname,stdio:["pipe","pipe","inherit"]});
  const lines=createInterface({input:server.stdout});
  let browser;
  try {
    const [port]=await once(lines,"line");
    browser=await chromium.launch({executablePath:process.env.PW_CHROMIUM||undefined});
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    await page.goto("http://127.0.0.1:"+port);
    const click=(name,scope=page)=>scope.getByRole("button",{name,exact:true}).first().click();
    const dlg=page.locator("#modal[open]");
    const pendingCard=page.locator("#preview .history .pending-version");
    async function checkZoom(thumbnail){
      await thumbnail.click();
      const viewer=page.locator("#image-dialog[open]");
      await viewer.waitFor();
      const src=await viewer.locator("img").evaluate(async img=>{await img.decode();return img.src;});
      assert.ok(!src.includes("?thumb"));
      await click("原始尺寸",viewer);
      assert.equal(await viewer.locator("[aria-pressed=true]").count(),1);
      await page.keyboard.press("Escape");
      await viewer.waitFor({state:"hidden"});
    }
    const styleFile=name=>({name,mimeType:"image/png",
      buffer:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAABgAAAAgCAIAAACHPC9vAAAAMklEQVR4nGPMbK9koAZgooopDKMGEQNGDSIMRg0iDEYNIgxGDSIMRg0iDEYNIgwGn0EACTABqeWRtD0AAAAASUVORK5CYII=","base64")});
    async function newProject(name,withStyle=false){
      await click("创建项目");await dlg.getByLabel("项目名称").fill(name);
      if(withStyle){
        await dlg.locator("[name=style_image]").setInputFiles([styleFile("a.png"),styleFile("b.png")]);
        await checkZoom(dlg.locator(".style-preview img").nth(1));
        assert.equal(await dlg.getByLabel("项目名称").inputValue(),name);
      }
      await click("创建并进入角色库",dlg);
      await page.getByRole("heading",{name,exact:true}).waitFor();
    }
    async function item(kind,name,brief,mode){
      await click("新建"+kind);await dlg.getByLabel(kind+"名称").fill(name);
      await dlg.locator("textarea").fill(brief);
      if(mode)await dlg.getByLabel("角色参考图模式").selectOption(mode);
      await click("创建"+kind,dlg);
      await page.getByRole("heading",{name,exact:true}).waitFor();
    }
    async function generate(){
      await click("生成候选图");
      await pendingCard.waitFor();
      assert.match(await pendingCard.innerText(),/生成中/);
      await page.locator("#preview .vthumb.sel").waitFor();
      await page.getByRole("button",{name:"采用此版",exact:true}).click();
      await page.getByRole("button",{name:"已采用",exact:true}).waitFor();
    }
    const goTab=label=>page.locator("#sidebar").getByRole("link",{name:label,exact:true}).click();
    await newProject("清晨的船",true);
    await click("项目设置");await checkZoom(dlg.locator(".style-preview img").first());
    await dlg.locator("[name=style_image]").setInputFiles(styleFile("c.png"));
    await dlg.locator("[name=style_image]").setInputFiles(styleFile("d.png"));
    await click("移除画风参考图 2",dlg);await click("保存项目",dlg);
    await dlg.waitFor({state:"hidden"});await page.reload();await click("项目设置");
    assert.equal(await dlg.locator(".style-preview img").count(),3);
    await click("关闭",dlg);
    await item("角色","船长","短发与蓝色雨衣");await generate();
    await checkZoom(page.locator(".viewer img"));
    await page.getByLabel("本次视角").selectOption("背面全身");
    await page.locator("#generate summary").click();await page.getByLabel("本次补充要求").fill("等待测试放行");
    await click("生成候选图");await pendingCard.waitFor();
    assert.equal(await pendingCard.locator(".pending-spinner").evaluate(el=>getComputedStyle(el).animationName),"candidate-spin");
    await page.getByLabel("本次视角").selectOption("主图");await pendingCard.waitFor({state:"hidden"});
    await page.reload();await pendingCard.waitFor();assert.equal(await page.getByLabel("本次视角").inputValue(),"背面全身");
    await page.emulateMedia({reducedMotion:"reduce"});
    assert.equal(await pendingCard.locator(".pending-spinner").evaluate(el=>getComputedStyle(el).animationName),"none");
    await page.emulateMedia({reducedMotion:"no-preference"});
    await page.evaluate(()=>fetch("/test/release-image",{method:"POST"}));
    await pendingCard.waitFor({state:"hidden"});await click("采用此版");await page.getByRole("button",{name:"已采用",exact:true}).waitFor();
    await page.locator("#generate summary").click();await page.getByLabel("本次补充要求").fill("测试生成失败");
    await click("生成候选图");await pendingCard.waitFor();await pendingCard.waitFor({state:"hidden"});
    assert.equal(await page.locator("#job-message").innerText(),"测试生成失败");
    assert.equal(await page.locator("#preview [data-version]").count(),1);
    await item("角色","旅客","米色长外套","three");
    const angle=name=>page.getByRole("radio",{name,exact:true});
    assert.ok(await angle("正视图").isChecked());
    assert.ok(await angle("侧视图").isDisabled() && await angle("背视图").isDisabled());
    await click("生成正视图候选");await pendingCard.waitFor();await page.locator("#preview .vthumb.sel").waitFor();
    assert.ok(await angle("侧视图").isDisabled());
    await click("采用此版");await page.getByRole("button",{name:"已采用",exact:true}).waitFor();
    await angle("侧视图").check();await generate();
    await click("版本信息");assert.match(await dlg.locator(".ref-records li").first().innerText(),/已采用的正视图/);await click("关闭",dlg);
    await angle("背视图").check();await generate();
    await page.reload();await angle("正视图").waitFor();
    assert.equal(await page.locator(".angle-card small").allTextContents().then(s=>s.join("/")),"已采用/已采用/已采用");
    await page.getByLabel("角色参考图模式").selectOption("single");await page.getByLabel("本次视角").selectOption("脸部近景");
    await page.getByLabel("角色参考图模式").selectOption("three");await click("保存设定");
    await page.getByText("设定已保存。",{exact:true}).waitFor();
    await goTab("场景库");await item("场景","码头","旧木栈桥");await generate();
    await goTab("分镜台");await item("分镜","相遇","两人在栈桥碰面");
    await checkZoom(page.locator(".actor img").first());
    assert.equal(await page.locator("#editor input:checked").count(),0);
    await page.locator("#editor").getByLabel("船长",{exact:true}).check();
    await page.getByLabel("船长参考视角").selectOption("背面全身");
    await page.locator("#editor").getByLabel("旅客",{exact:true}).check();
    await page.locator("#editor select[name=scene]").selectOption({label:"码头"});
    await page.getByLabel("图1 · 码头 · 主图",{exact:true}).fill("保持木栈桥的方向");
    await generate();
    await click("版本信息");
    assert.match(await dlg.locator(".ref-records li").first().innerText(),/码头 · 主图\s+保持木栈桥的方向/);
    assert.match(await dlg.locator(".ref-records li").nth(1).innerText(),/船长 · 背面全身/);
    assert.match(await dlg.locator(".ref-records li").nth(2).innerText(),/旅客 · 正面全身/);
    assert.match(await dlg.innerText(),/项目画风参考图/);
    await click("关闭",dlg);
    await click("局部返修");await dlg.locator("canvas").click({position:{x:12,y:20}});
    await dlg.getByLabel("修改要求").fill("修复一个扣子");await click("生成一张返修候选",dlg);
    await pendingCard.waitFor();assert.match(await pendingCard.innerText(),/V02 · 生成中/);
    await page.waitForFunction(()=>document.querySelectorAll("#preview [data-version]").length===2);
    await click("采用此版");await page.getByRole("button",{name:"已采用",exact:true}).waitFor();
    const originalDraft=page.locator("#preview .vthumb").filter({hasText:"V01"});
    await originalDraft.press("Enter");
    const repairBase=await page.locator("#preview .viewer img").getAttribute("src");
    await click("局部返修");
    await click("生成一张返修候选",dlg);await dlg.getByText("请填写修改要求。").waitFor();
    await dlg.getByLabel("修改要求").fill("将雨衣改成深蓝色");
    await click("生成一张返修候选",dlg);await pendingCard.waitFor();
    await page.waitForFunction(()=>document.querySelectorAll("#preview [data-version]").length===3);
    assert.match(await page.locator("#preview .vthumb.sel").innerText(),/V03 · 候选/);
    assert.match(await page.locator("#preview .vthumb").filter({hasText:"V02"}).innerText(),/定稿/);
    await click("版本信息");await dlg.getByText("整图返修",{exact:true}).waitFor();await click("关闭",dlg);
    await originalDraft.press("Enter");
    assert.equal(await page.locator("#preview .viewer img").getAttribute("src"),repairBase);
    await item("分镜","空镜","海面远景，无人");
    assert.equal(await page.locator("#editor input:checked").count(),0);
    const earlier=page.getByLabel("前序分镜参考图");
    await earlier.selectOption({label:"S01 · 相遇 · V01 · 候选"});
    const previousNote=page.getByLabel("图1 · S01 · 相遇 · V01 · 候选",{exact:true});
    await previousNote.fill("沿用天际线的位置");
    await page.getByLabel("图2 · 项目画风参考图 1",{exact:true}).fill("沿用清晨的灰蓝色");
    await click("保存设定");await page.getByText("设定已保存。",{exact:true}).waitFor();
    await page.reload();await previousNote.waitFor();assert.equal(await previousNote.inputValue(),"沿用天际线的位置");
    await page.locator("#generate summary").click();
    await page.locator("#generate [name=reference]").setInputFiles(styleFile("tide.png"));
    await page.getByLabel("上传参考图的补充说明").fill("参考潮水高度");
    await generate();
    await click("版本信息");
    assert.match(await dlg.locator(".ref-records li").first().innerText(),/S01 · 相遇 · V01\s+沿用天际线的位置/);
    assert.match(await dlg.locator(".ref-records li").last().innerText(),/本次上传参考图\s+参考潮水高度/);
    await click("关闭",dlg);
    await click("前移");await page.getByText(/请先在「空镜」移除对应参考图/).waitFor();
    await click("移除S01 · 相遇 · V01 · 候选");
    await click("前移");await page.waitForFunction(()=>document.querySelector(".eyeline.quiet")?.textContent.includes("01 / 02"));
    await page.reload();await page.getByRole("heading",{name:"空镜",exact:true}).waitFor();
    await goTab("分镜台");
    await checkZoom(page.locator(".poster img").first());
    const download=page.waitForEvent("download");await page.getByRole("link",{name:"导出分镜"}).click();
    assert.equal((await download).suggestedFilename(),"分镜成片.zip");
    await goTab("项目库");await checkZoom(page.locator(".strip img").first());
    await checkZoom(page.locator(".feature img").first());await newProject("第二部");
    await page.getByRole("heading",{name:"添加第一个角色"}).waitFor();
    await goTab("项目库");await page.locator(".projecttile time").waitFor();
    const created=await page.locator(".feature time").getAttribute("datetime");
    const projectActions=name=>page.getByLabel(name+"的项目操作",{exact:true}).click();
    await projectActions("第二部");await click("重命名");await dlg.getByLabel("项目名称").fill("归档示例");
    await click("保存名称",dlg);await page.getByRole("heading",{name:"归档示例"}).waitFor();
    assert.equal(await page.locator(".feature time").getAttribute("datetime"),created);
    await projectActions("归档示例");await click("归档");
    await page.getByRole("heading",{name:"清晨的船",exact:true}).waitFor();await click("设置");
    await click("恢复",dlg);await dlg.getByText("暂无归档项目").waitFor();await click("关闭",dlg);
    await projectActions("归档示例");await click("归档");
    await page.getByLabel("归档示例的项目操作").waitFor({state:"hidden"});await click("设置");
    page.once("dialog",d=>d.dismiss());await click("彻底删除",dlg);
    page.once("dialog",d=>d.accept());await click("彻底删除",dlg);
    await dlg.getByText("暂无归档项目").waitFor();await click("关闭",dlg);
    assert.equal(await page.getByRole("link",{name:"第二部",exact:true}).count(),0);
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await click("展开导航");await goTab("清晨的船");
    await page.getByRole("heading",{name:"清晨的船",exact:true}).waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await checkZoom(page.locator(".poster img").first());
    await page.setViewportSize({width:1440,height:900});
    await goTab("项目库");
    const draft=JSON.parse(readFileSync(join(__dirname,"static","映序导入示例.json"),"utf8"));
    const generationRequests=[];
    page.on("request",request=>{if(request.url().endsWith("/generate"))generationRequests.push(request.url());});
    const enterDraft=dlg.getByLabel("文本内容",{exact:true});
    const importButton="解析并创建项目";
    await click("导入项目");
    await enterDraft.fill("{");
    await click(importButton,dlg);
    await dlg.getByText(/第 1 行/).waitFor();
    assert.equal(await enterDraft.inputValue(),"{");
    await enterDraft.fill(JSON.stringify(draft));
    await click(importButton,dlg);
    await page.getByRole("heading",{name:"末班船票",exact:true}).waitFor();
    await page.locator(".card-body").filter({hasText:"灯下交票"}).click();
    assert.equal(await page.getByLabel("阿青参考视角").inputValue(),"正面全身");
    assert.equal(await page.locator("[name=scene] option:checked").textContent(),"渡口");
    assert.equal(await page.locator("[name=voice]").inputValue(),"那是最后一班船。");
    await goTab("角色库");await page.locator(".card-body").filter({hasText:"阿青"}).click();
    assert.ok(await angle("正视图").isChecked() && await angle("侧视图").isDisabled());
    assert.equal(await page.getByLabel("本次补充要求").inputValue(),draft.characters[0].viewNotes["正面全身"]);
    await goTab("项目库");await click("导入项目");
    draft.project.name="文件渡口";
    await dlg.getByLabel("读取文本文件").setInputFiles({name:"故事.md",mimeType:"text/markdown",
      buffer:Buffer.from("\ufeff```json\n"+JSON.stringify(draft)+"\n```")});
    await page.waitForFunction(()=>document.querySelector("#import-form textarea").value.includes("文件渡口"));
    await click(importButton,dlg);
    await page.getByRole("heading",{name:"文件渡口",exact:true}).waitFor();
    await goTab("项目库");await click("导入项目");
    await dlg.getByLabel("文本类型").selectOption("story");
    await enterDraft.fill("需要校对的原文");
    assert.ok(await dlg.getByRole("button",{name:importButton}).isDisabled());
    await click("打开设置",dlg);
    await dlg.getByLabel("文本模型 ID（故事原文拆镜）").fill("story-test");
    await click("保存配置",dlg);
    await enterDraft.waitFor();assert.equal(await enterDraft.inputValue(),"需要校对的原文");
    await click(importButton,dlg);await dlg.getByText(/拆镜稿需要修改/).waitFor();
    assert.equal(await enterDraft.inputValue(),"{}");
    await dlg.getByLabel("文本类型").selectOption("story");
    assert.equal(await enterDraft.inputValue(),"需要校对的原文");
    await enterDraft.fill("原文拆镜项目");await click(importButton,dlg);
    await page.getByRole("heading",{name:"原文拆镜项目",exact:true}).waitFor();
    assert.deepEqual(generationRequests,[]);
    if(process.env.FRAMEBOOK_PREVIEW_DIR){
      await page.setViewportSize({width:1440,height:900});
      await page.route("http://127.0.0.1:8765/**",route=>route.request().method()==="GET"?route.continue():route.abort());
      await page.goto("http://127.0.0.1:8765");
      await page.locator(".feature img").last().waitFor();
      await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode().catch(()=>{}))));
      await page.screenshot({path:join(process.env.FRAMEBOOK_PREVIEW_DIR,"framebook-library.png")});
      await page.locator(".strip a").first().click();
      await page.locator("#preview img").first().waitFor();
      await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode().catch(()=>{}))));
      await page.screenshot({path:join(process.env.FRAMEBOOK_PREVIEW_DIR,"framebook-shot.png")});
    }
    assert.deepEqual(errors,[]);console.log("PASS browser: paste/file project import, story analysis, manual generation, style upload, zoom, archive, repair, export, reload, mobile.");
  }finally{
    await browser?.close();
    lines.close();server.stdin.end("\n");
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
