"""Integration checks. All image-provider traffic is replaced with a local test double."""
import base64
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from PIL import Image
from app import create_app
from credentials import save_config
from imaging import png_bytes, request_image
from storage import database, initialize


class ProductionFlow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        initialize(self.root)
        self.client = create_app(self.root).test_client()
        self.origin = {"Origin": "http://localhost"}
        save_config(self.root/"service.dpapi", {"base": "https://provider.invalid/v1", "model": "fixture", "key": "fixture-key"})
        self.calls = []

        def provider(config, prompt, images):
            self.calls.append((prompt, [im.getpixel((0, 0)) for im in images]))
            return Image.new("RGB", (80, 100), "red")
        stub = patch("imaging.request_image", side_effect=provider)
        stub.start()
        self.addCleanup(stub.stop)

    def post(self, path, data=None):
        r = self.client.post(path, json=data or {}, headers=self.origin)
        self.assertLess(r.status_code, 300, r.data)
        return r.json

    def project(self, name):
        return self.post("/api/projects", {"name":name,"style":"低饱和纪实","ratio":"3:4"})["id"]

    def item(self, p, kind, name, **kwargs):
        return self.post(f"/api/projects/{p}/items", {"kind":kind,"name":name,"brief":"测试画面",**kwargs})["id"]

    def run_image(self, iid, *, view="主图", **fields):
        jid = uuid4().hex
        r = self.client.post(f"/api/items/{iid}/generate", data={"view":view,"rev":"1","request":jid,**fields},headers=self.origin)
        self.assertEqual(r.status_code, 202, r.data)
        pending = self.client.get("/api/jobs/"+jid).json
        for _ in range(200):
            if pending["state"] != "running":
                break
            time.sleep(.02)
            pending = self.client.get("/api/jobs/"+jid).json
        self.assertEqual(pending["state"], "done", pending)
        return pending["version"], jid

    def adopt(self, vid):
        self.post(f"/api/versions/{vid}/adopt")

    def archive(self, pid):
        path = f"/api/projects/{pid}"
        rev = self.client.get(path).json["project"]["rev"]
        self.post(path+"/manage", {"action":"archive", "rev":rev})

    def test_import_skill_draft_creates_editable_project_without_images(self):
        path = Path(__file__).parent / "static" / "映序导入示例.json"
        example = path.read_text(encoding="utf-8")
        skill_example = path.parents[2] / ".agents/skills/framebook-story-prompts/references" / path.name
        self.assertEqual(json.loads(example), json.loads(skill_example.read_text(encoding="utf-8")))
        payload = {"text":"\ufeff# 映序导入稿\r\n```json\r\n"+example+"\r\n```\r\n请核对镜序。", "request": uuid4().hex}
        result = self.post("/api/projects/import", payload)
        self.assertEqual(result["counts"], {"character":2,"scene":1,"shot":2})
        self.assertEqual(self.post("/api/projects/import", payload), result)
        self.assertEqual(len(self.client.get("/api/projects").json["projects"]), 1)
        project = self.client.get("/api/projects/"+result["id"]).json
        self.assertEqual(project["project"]["name"], "末班船票")
        self.assertEqual(project["project"]["ratio"], "16:9")
        self.assertEqual(project["project"]["style"], json.loads(example)["project"]["style"])
        items = project["items"]
        self.assertTrue(all(not i["versions"] and i["job"] is None for i in items))
        cast = [i for i in items if i["kind"]=="character"]
        scene = next(i for i in items if i["kind"]=="scene")
        first, second = [i for i in items if i["kind"]=="shot"]
        self.assertEqual([first["rank"],second["rank"]], [1,2])
        self.assertEqual(first["cast"], [{"id":cast[0]["id"],"view":"正面全身"},{"id":cast[1]["id"],"view":"主图"}])
        self.assertEqual([first["scene"], second["scene"], second["sceneView"]], [scene["id"],scene["id"],"仅文字"])
        self.assertEqual(second["cast"], [])
        self.assertEqual(first["voice"], "那是最后一班船。")
        self.assertTrue(first["camera"])
        self.assertFalse(self.calls)
        self.run_image(scene["id"])
        self.assertIn(scene["viewNotes"]["主图"], self.calls[0][0])

    def test_import_invalid_draft_rolls_back(self):
        def reject(draft, message):
            result = self.client.post("/api/projects/import", json={"request":uuid4().hex,
                "text":json.dumps(draft, ensure_ascii=False)}, headers=self.origin)
            self.assertEqual(result.status_code, 400, result.data)
            self.assertIn(message, result.json["error"])
            self.assertFalse(self.client.get("/api/projects").json["projects"])
        draft = {"format":"framebook.project.v1","project":{"name":"空镜"},"characters":[{"name":"甲"}],
                 "scenes":[],"shots":[{"name":"第一镜"},{"name":"第二镜","cast":[{"name":"乙"}]}]}
        reject(draft, "分镜第 2 项")
        draft["characters"][0]["referenceMode"] = "three"
        draft["shots"][1]["cast"] = [{"name":"甲","view":"主图"}]
        reject(draft, "不支持视角")
        draft["shots"][1].update(cast=[], scene={"name":"不存在"})
        reject(draft, "未声明")
        draft["shots"][1].update(scene=None)
        draft["characters"].append({"name":"甲"})
        reject(draft, "重复")
        draft["characters"] = []
        draft["shots"][1]["generate"] = True
        reject(draft, "不支持的字段")
        draft["project"]["ratio"] = []
        reject(draft, "画幅")
        draft["shots"] = "镜一"
        reject(draft, "数组")
        draft["format"] = "framebook.project.v2"
        reject(draft, "格式")
        self.assertFalse(self.calls)

    def test_import_story_uses_only_configured_text_model(self):
        payload = {"mode":"story","request":uuid4().hex,"text":"阿青赶到渡口。"}
        path = "/api/projects/import"
        self.assertIn("文本模型 ID", self.client.post(path,json=payload,headers=self.origin).json["error"])
        self.post("/api/config", {"base":"https://provider.invalid/v1","model":"fixture","textModel":"story-model"})
        answer = Mock(status_code=200)
        draft = {"format":"framebook.project.v1", "project":{"name":"渡船"}, "characters":[], "scenes":[],
                 "shots":[{"name":"到达","brief":"阿青站在跳板前。","scene":None}]}
        answer.json.return_value = {"choices":[{"message":{"content":json.dumps(draft)}}]}
        with patch("story_import.requests.post", return_value=answer) as post:
            result = self.post(path, payload)
            self.assertEqual(self.post(path,payload), result)
            post.assert_called_once()
            self.assertEqual(post.call_args.args[0], "https://provider.invalid/v1/chat/completions")
            body = post.call_args.kwargs["json"]
            self.assertEqual(body["model"], "story-model")
            self.assertEqual(body["messages"][-1], {"role":"user","content":payload["text"]})
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            payload["request"] = uuid4().hex
            answer.json.return_value["choices"][0]["message"]["content"] = "{}"
            failed = self.client.post(path,json=payload,headers=self.origin)
            self.assertEqual((failed.status_code,failed.json["draft"]), (400,"{}"))
            answer.status_code = 401
            self.assertIn("HTTP 401", self.client.post(path,json=payload,headers=self.origin).json["error"])
        self.assertEqual(len(self.client.get("/api/projects").json["projects"]),1)
        self.assertFalse(self.calls)

    def test_deleting_project_while_provider_is_rendering(self):
        pid = self.project("正在生成的项目")
        iid = self.item(pid, "character", "甲")
        jid = uuid4().hex
        with patch("app.threading.Thread") as thread:
            response = self.client.post(f"/api/items/{iid}/generate", data={"view":"主图", "rev":"1", "request":jid}, headers=self.origin)
        self.assertEqual(response.status_code, 202)

        def finish_after_deletion(*args, **kwargs):
            self.archive(pid)
            self.assertEqual(self.client.delete(f"/api/projects/{pid}", headers=self.origin).status_code, 200)
            return Image.new("RGB", (30,40))

        worker = thread.call_args.kwargs
        with patch("app.render", side_effect=finish_after_deletion) as render:
            worker["target"](*worker["args"])
            render.assert_called_once()
        self.assertEqual(self.client.get("/api/jobs/"+jid).status_code, 404)
        self.assertEqual(list((self.root/"media").iterdir()), [])

    def test_project_style_and_deletion(self):
        def picture(color):
            return io.BytesIO(png_bytes(Image.new("RGB", (35, 45), color))), "ref.png"

        res = self.client.post("/api/projects", data={"name":"风格测试", "style_image":(io.BytesIO(b"bad"), "bad.png")}, headers=self.origin)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get("/api/projects").json["projects"], [])
        res = self.client.post("/api/projects", data={"name":"风格测试", "style_image":picture("lime")}, headers=self.origin)
        self.assertEqual(res.status_code, 201)
        pid = res.json["id"]
        path = f"/api/projects/{pid}"
        style = self.client.get(path).json["project"]["style_ref"]
        with self.client.get("/media/"+style) as response, Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual(image.getpixel((0,0)), (0,255,0))
        with self.client.get("/media/"+style+"?thumb=1") as response:
            self.assertEqual(response.mimetype, "image/webp")
        actor = self.item(pid, "character", "甲")
        main, job = self.run_image(actor, reference=picture("blue"))
        self.assertEqual(self.calls[-1][1], [(0,255,0), (0,0,255)])
        self.assertIn("中性纯色底", self.calls[-1][0])
        self.assertIn("图2是本次补充参考", self.calls[-1][0])
        self.adopt(main)
        self.run_image(actor, view="脸部近景", direction="背景指定在海边")
        self.assertEqual(self.calls[-1][1], [(255,0,0), (0,255,0)])
        self.assertIn("按指定背景绘制", self.calls[-1][0])
        self.assertIn("背景指定在海边", self.calls[-1][0])
        self.assertNotIn("三分之四正面全身", self.calls[-1][0])
        shot = self.item(pid, "shot", "合照", cast=[{"id":actor, "view":"主图"}])
        vid,_ = self.run_image(shot, view="分镜")
        self.assertEqual(self.calls[-1][1], [(255,0,0), (0,255,0)])
        self.assertNotIn("中性纯色底", self.calls[-1][0])
        mask = Image.new("L", (75,100), 255)
        self.run_image(shot, view="分镜", parent=vid, direction="改衣领", mask=(io.BytesIO(png_bytes(mask)),"mask.png"))
        self.assertEqual(self.calls[-1][1], [(255,0,0), (0,255,0)])
        other = self.project("另一个项目")
        other_image,_ = self.run_image(self.item(other, "scene", "海边"))
        self.assertFalse(self.calls[-1][1])
        self.assertNotIn("中性纯色底", self.calls[-1][0])
        first_project = self.client.get(path).json["project"]
        settings = {"rev":1, "name":"新名", "style_image":[picture("black"),picture("yellow")]}
        self.assertEqual(self.client.patch(path, data=settings, headers=self.origin).status_code, 200)
        updated = self.client.get(path).json["project"]
        self.assertEqual(updated["created"], first_project["created"])
        self.assertEqual(updated["folder"], first_project["folder"])
        self.assertEqual(len(updated["style_refs"]),3)
        self.assertEqual(updated["style_refs"][0],style)
        self.run_image(actor)
        self.assertEqual(self.calls[-1][1],[(0,255,0),(0,0,0),(255,255,0)])
        updated = self.client.get(path).json["project"]
        invalid = {"rev":2, "name":"出错", "remove_style":style, "style_image":[picture("red"),(io.BytesIO(b"bad"),"bad.png")]}
        before_files = set((self.root/"media").rglob("*.*"))
        self.assertEqual(self.client.patch(path,data=invalid,headers=self.origin).status_code,400)
        self.assertEqual(set((self.root/"media").rglob("*.*")),before_files)
        self.assertEqual(self.client.get(path).json["project"],updated)
        self.assertEqual(self.client.patch(path,data={"rev":2,"name":"新名","remove_style":style},headers=self.origin).status_code,200)
        self.assertEqual(self.client.get("/media/"+style).status_code, 404)
        new_style = self.client.get(path).json["project"]["style_ref"]
        self.assertNotEqual(new_style, style)
        self.assertEqual(self.client.delete(path, headers=self.origin).status_code, 409)
        self.archive(pid)
        listed = self.client.get("/api/projects").json
        self.assertEqual([p["id"] for p in listed["archived"]],[pid])
        self.assertEqual([p["id"] for p in listed["projects"]],[other])
        self.post(path+"/manage",{"action":"restore","rev":listed["archived"][0]["rev"]})
        self.assertEqual(self.client.get(path).json["project"]["created"],first_project["created"])
        self.archive(pid)
        self.assertEqual(self.client.delete(path, headers=self.origin).status_code, 200)
        for url in [path, "/media/"+main, "/media/"+new_style, "/api/jobs/"+job]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertFalse((self.root/"media"/first_project["folder"]).exists())
        self.assertEqual({p.stem for p in (self.root/"media").rglob("*") if p.is_file()}, {other_image})
        self.assertEqual(self.client.get(f"/api/projects/{other}").status_code, 200)
        self.archive(other)
        self.client.delete(f"/api/projects/{other}", headers=self.origin)
        with patch("storage.seed") as seed:
            example = self.root/"example"
            (example/"analysis").mkdir(parents=True)
            (example/"analysis/manual_visual_review.json").touch()
            initialize(self.root, example)
            seed.assert_not_called()
        with database(self.root) as db:
            for table in ("projects", "items", "versions", "jobs", "style_refs", "media_cleanup"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_legacy_style_migration(self):
        root = self.root/"legacy"
        root.mkdir()
        with database(root) as db:
            db.executescript("""
                CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT,style TEXT,ratio TEXT,source TEXT,modified REAL,rev INTEGER DEFAULT 1);
                CREATE TABLE project_styles (project TEXT PRIMARY KEY,id TEXT UNIQUE,image BLOB,thumb BLOB);
                INSERT INTO projects VALUES ('old','旧项目','','3:4','',123,1);
                PRAGMA user_version=1;
            """)
            db.execute("INSERT INTO project_styles VALUES(?,?,?,?)", ("old","ref",b"original",b"thumb"))
        initialize(root)
        initialize(root)
        client = create_app(root).test_client()
        project = client.get("/api/projects/old").json["project"]
        self.assertEqual(project["style_refs"], ["ref"])
        self.assertTrue(project["created_estimated"])
        folder=root/"media"/project["folder"]/"画风"
        self.assertEqual((folder/"ref.png").read_bytes(), b"original")
        self.assertEqual((folder/"ref.webp").read_bytes(), b"thumb")

    def test_earlier_shot_versions_and_individual_notes(self):
        p = self.project("接镜")
        first = self.item(p,"shot","出门")
        second = self.item(p,"shot","关门")
        actor = self.item(p,"character","旅客")
        scene = self.item(p,"scene","门厅")

        def picture(color):
            return io.BytesIO(png_bytes(Image.new("RGB",(60,80),color))), "ref.png"

        def upload(iid, view, color):
            res = self.client.post(f"/api/items/{iid}/upload",data={"view":view,"file":picture(color)},headers=self.origin)
            self.assertEqual(res.status_code,200)
            return res.json["id"]

        one = upload(first,"分镜","blue")
        alternate = upload(first,"分镜","white")
        two = upload(second,"分镜","lime")
        face = upload(actor,"主图","red")
        hall = upload(scene,"主图","black")
        for vid in (one,two,face,hall):
            self.adopt(vid)
        self.client.patch(f"/api/projects/{p}",data={"name":"接镜","rev":1,"style_image":picture("yellow")}, headers=self.origin)
        style = self.client.get(f"/api/projects/{p}").json["project"]["style_ref"]
        refs = [hall,face,two,one,style]
        notes = dict(zip(refs,["保留门框","保留领口","保持手位","沿用天色","参考配色"]))
        target = self.item(p,"shot","回望",cast=[{"id":actor,"view":"主图"}],scene=scene,shotRefs=[two,one],imageNotes=notes)
        self.adopt(alternate)
        version,_ = self.run_image(target,view="分镜",reference=picture("cyan"),uploadNote="参考构图")
        prompt,pixels = self.calls[-1]
        self.assertEqual(pixels,[(0,0,0),(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255)])
        for index,note in enumerate([*notes.values(),"参考构图"],1):
            self.assertIn(f"图{index}补充说明：{note}",prompt)
        items = self.client.get(f"/api/projects/{p}").json["items"]
        meta = next(i for i in items if i["id"]==target)["versions"][0]["meta"]
        self.assertEqual(meta["refs"],refs)
        self.assertEqual([r["note"] for r in meta["refDetails"]],[*notes.values(),"参考构图"])
        self.assertIn("S01 · 出门 · V01",meta["refDetails"][3]["label"])
        stranger = self.item(self.project("另一部"),"shot","别处")
        foreign = upload(stranger,"分镜","red")
        for iid,invalid in [(first,[two]),(first,[one]),(target,[foreign]),(target,[face]),(target,[one,one]),(target,[version])]:
            res = self.client.patch(f"/api/items/{iid}",json={"rev":1,"name":"测试","shotRefs":invalid},headers=self.origin)
            self.assertEqual(res.status_code,400)
        res = self.client.post(f"/api/items/{second}/move",json={"delta":1},headers=self.origin)
        self.assertEqual(res.status_code,409)
        export = self.client.get(f"/api/projects/{p}/export")
        with zipfile.ZipFile(io.BytesIO(export.data)) as archive:
            shots = json.loads(archive.read("分镜清单.json"))["shots"]
        self.assertEqual([(r["id"],r["note"]) for r in shots[-1]["shotRefs"]],[(two,notes[two]),(one,notes[one])])

    def test_three_views_derive_only_from_adopted_front(self):
        pid = self.project("人物设定")
        actor = self.item(pid, "character", "船长", referenceMode="three")
        path = f"/api/items/{actor}/generate"

        def request_back(**fields):
            return self.client.post(path, data={"view":"背面全身","rev":"1","request":uuid4().hex,**fields}, headers=self.origin)

        self.assertEqual(request_back().status_code,400)
        front,_ = self.run_image(actor,view="正面全身")
        self.assertIn("正对镜头",self.calls[-1][0])
        self.assertEqual(self.calls[-1][1],[])
        self.assertEqual(request_back().status_code,400)
        self.assertEqual(len(self.calls),1)
        self.adopt(front)
        side,_ = self.run_image(actor,view="侧面全身",frontVersion=front)
        self.assertEqual(self.calls[-1][1],[(255,0,0)])
        self.assertIn("严格侧面",self.calls[-1][0])
        self.adopt(side)
        source = io.BytesIO(png_bytes(Image.new("RGB",(60,80),"blue")))
        replacement = self.client.post(f"/api/items/{actor}/upload",data={"view":"正面全身","file":(source,"front.png")},headers=self.origin).json["id"]
        self.adopt(replacement)
        self.assertEqual(request_back(frontVersion=front).status_code,409)
        back,_ = self.run_image(actor,view="背面全身",frontVersion=replacement)
        self.assertEqual(self.calls[-1][1],[(0,0,255)])
        self.assertIn("背对镜头",self.calls[-1][0])
        versions = self.client.get(f"/api/projects/{pid}").json["items"][0]["versions"]
        by_id = {v["id"]:v for v in versions}
        for vid,base in ((side,front),(back,replacement)):
            self.assertEqual(by_id[vid]["meta"]["refs"],[base])
            self.assertEqual(by_id[vid]["meta"]["frontVersion"],base)
        self.assertEqual(by_id[side]["adopted"],1)
        self.assertEqual(by_id[back]["adopted"],0)
        self.assertEqual(self.client.get("/api/projects").json["projects"][0]["covers"],[replacement])
        shot = self.item(pid,"shot","起航",cast=[{"id":actor}])
        self.run_image(shot,view="分镜")
        self.assertEqual(self.calls[-1][1],[(0,0,255)])

    def test_repair_with_optional_mask_keeps_original(self):
        pid = self.project("返修")
        iid = self.item(pid, "character", "旅客")
        view = "侧面全身"
        original = Image.new("RGB", (61,37), "blue")
        res = self.client.post(f"/api/items/{iid}/upload", data={"view":view,
                               "file":(io.BytesIO(png_bytes(original)),"source.png")}, headers=self.origin)
        source = res.json["id"]
        self.adopt(source)
        with self.client.get("/media/"+source) as response:
            before = response.data
        with patch("imaging.request_image",return_value=Image.new("RGB",(80,100),"red")) as provider:
            whole,_ = self.run_image(iid,parent=source,direction="把衣服改为红色")
            _,prompt,images = provider.call_args.args
            self.assertIn("完整原图",prompt)
            self.assertIn("把衣服改为红色",prompt)
            self.assertEqual(len(images),1)
            self.assertEqual(images[0].size,original.size)
            self.assertEqual(images[0].tobytes(),original.tobytes())
        mask = Image.new("L",original.size,0)
        mask.putpixel((25,18),255)
        local,_ = self.run_image(iid,parent=source,direction="修复扣子",mask=(io.BytesIO(png_bytes(mask)),"mask.png"))
        for vid,corner in ((whole,(255,0,0)),(local,(0,0,255))):
            with self.client.get("/media/"+vid) as response, Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.size,original.size)
                self.assertEqual(image.getpixel((0,0)),corner)
                self.assertEqual(image.getpixel((25,18)),(255,0,0))
        with self.client.get("/media/"+source) as response:
            self.assertEqual(response.data,before)
        versions = self.client.get(f"/api/projects/{pid}").json["items"][0]["versions"]
        self.assertEqual({v["id"] for v in versions},{source,whole,local})
        self.assertEqual([v["id"] for v in versions if v["adopted"]],[source])
        for v in versions:
            self.assertEqual(v["view"],view)
            if v["id"] != source:
                self.assertEqual(v["meta"]["parent"],source)
                self.assertEqual(v["origin"],"整图返修" if v["id"]==whole else "局部返修")
        other = self.item(pid,"character","船长")
        for target,direction in [(iid,"  "),(other,"改颜色")]:
            res = self.client.post(f"/api/items/{target}/generate", data={"rev":"1","request":uuid4().hex,
                                   "parent":source,"direction":direction},headers=self.origin)
            self.assertEqual(res.status_code,400)

    def test_studio(self):
        p, q = self.project("第一部"), self.project("第二部")
        cast = [self.item(p,"character",name) for name in ("甲","乙")]
        scene = self.item(p,"scene","庭院")
        alice, job = self.run_image(cast[0])
        self.adopt(alice)
        r = self.client.post(f"/api/items/{cast[0]}/generate",data={"view":"主图","rev":"1","request":job},headers=self.origin)
        self.assertEqual(r.status_code, 202)
        self.assertEqual(len(self.calls),1)
        side,_ = self.run_image(cast[0],view="侧面全身")
        self.adopt(side)
        self.assertEqual(len(self.calls[-1][1]),1)
        for iid in (cast[1],scene):
            buf = io.BytesIO(png_bytes(Image.new("RGB",(60,80),"blue")))
            res = self.client.post(f"/api/items/{iid}/upload",data={"view":"主图","file":(buf,"ref.png")},headers=self.origin)
            self.adopt(res.json["id"])
        shot = self.item(p,"shot","合照",cast=[{"id":cast[0],"view":"侧面全身"},{"id":cast[1],"view":"主图"}],
                         scene=scene,voice="测试旁白")
        vid,_ = self.run_image(shot,view="分镜")
        self.assertEqual(self.calls[-1][1],[(0,0,255),(255,0,0),(0,0,255)])
        self.assertIn("图2为角色「甲」的侧面全身",self.calls[-1][0])
        self.assertNotIn("测试旁白",self.calls[-1][0])
        self.adopt(vid)
        with self.client.get("/media/"+vid) as response, Image.open(io.BytesIO(response.data)) as img:
            self.assertEqual(img.size,(75,100))
            mask = Image.new("L",img.size,0)
        mask.putpixel((35,45),255)
        self.run_image(shot,view="分镜",parent=vid,direction="调整衣领",mask=(io.BytesIO(png_bytes(mask)),"mask.png"))
        blank = self.item(p,"shot","空镜")
        self.run_image(blank,view="分镜")
        self.assertFalse(self.calls[-1][1])
        self.post(f"/api/items/{blank}/move", {"delta":-1})
        archive = zipfile.ZipFile(io.BytesIO(self.client.get(f"/api/projects/{p}/export").data))
        entries = json.loads(archive.read("分镜清单.json"))["shots"]
        self.assertEqual([e["name"] for e in entries],["空镜","合照"])
        self.assertIn(entries[1]["file"],archive.namelist())
        self.assertEqual(entries[1]["cast"][0]["name"],"甲")
        other = self.client.post(f"/api/projects/{q}/items",json={"kind":"shot","name":"串用","cast":[{"id":cast[0]}]},headers=self.origin)
        self.assertEqual(other.status_code,400)
        self.assertEqual(self.client.get(f"/api/projects/{q}").json["items"],[])
        self.assertEqual(self.client.post("/api/projects",json={"name":"无来源"}).status_code,403)
        self.assertNotIn(b"fixture-key",self.client.get("/api/config").data)
        self.assertEqual(create_app(self.root).test_client().get("/api/projects").json["projects"][1]["id"],q)


class ProviderFormat(unittest.TestCase):
    def test_openai_images(self):
        pic = Image.new("RGB", (16, 16), "blue")
        conf = {"base": "https://provider.invalid/v1", "model": "test-id", "key": "test-key"}
        response = Mock(ok=True, is_redirect=False)
        response.json.return_value = {"data": [{"b64_json": base64.b64encode(png_bytes(pic)).decode()}]}
        with patch("imaging.requests.post", return_value=response) as post:
            for refs, route in [([], "generations"), ([pic], "edits"), ([pic, pic], "edits")]:
                self.assertEqual(request_image(conf, "test", refs).size, pic.size)
                args, kwargs = post.call_args
                self.assertEqual(args[0], conf["base"]+"/images/"+route)
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
                self.assertEqual(kwargs["data" if refs else "json"]["n"], 1)
                if refs:
                    self.assertEqual([f[0] for f in kwargs["files"]], ["image" if len(refs)==1 else "image[]"]*len(refs))
            self.assertEqual(post.call_count, 3)
            response.ok = False
            response.status_code = 503
            response.json.return_value = {"error":{"message":"unavailable"}}
            with self.assertRaisesRegex(ValueError, "503"):
                request_image(conf, "test", [])
            self.assertEqual(post.call_count, 4)


if __name__ == "__main__":
    unittest.main()
