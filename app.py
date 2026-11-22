import argparse
import io
import json
import os
import re
import threading
import time
import webbrowser
import zipfile
from contextlib import ExitStack
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import NAMESPACE_URL, UUID, uuid5

from flask import Flask, abort, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from credentials import read_config, save_config
from imaging import RATIOS, THREE_VIEWS, VIEWS, load_image, render, service_base
from story_import import import_items, parse_draft, read_text, split_story
from storage import (MEDIA_KINDS, STYLE, add_item, add_project, cleanup_deleted_media, database, delete_project,
                     encode, initialize, media_file, queue_removal, touch, uid, version, write_picture)

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "data"
KIND = {"character": "角色", "scene": "场景", "shot": "分镜"}
API_VERSION = 6


def string(value, maximum=12000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("文字过长或格式无效。")
    return value.strip()


def required(value, maximum=120):
    value = string(value, maximum)
    if not value:
        raise ValueError("请填写名称或画面描述。")
    return value


def row(db, table, ident):
    found = db.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
    if found is None:
        abort(404, "找不到所选项目或素材。")
    return dict(found)


def serialize_item(db, item):
    result = {**dict(item), **json.loads(item["data"])}
    del result["data"]
    result["versions"] = [{**dict(v), "meta": json.loads(v["meta"])} for v in db.execute(
        "SELECT id,view,adopted,origin,meta,created FROM versions WHERE item=? ORDER BY created DESC,rowid DESC", (item["id"],))]
    job = db.execute("SELECT * FROM jobs WHERE item=? ORDER BY created DESC LIMIT 1", (item["id"],)).fetchone()
    result["job"] = dict(job) if job else None
    return result


def clean_project(data):
    ratio = data.get("ratio", "3:4")
    if not isinstance(ratio, str) or ratio not in RATIOS:
        raise ValueError("请选择支持的画幅。")
    return required(data.get("name"), 80), string(data.get("style", "")), ratio


def project_form():
    if request.is_json:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("项目信息格式无效。")
        return data, [], []
    if request.mimetype not in ("multipart/form-data", "application/x-www-form-urlencoded"):
        abort(415, "请使用文件表单上传画风参考图，或使用 JSON 保存项目文字。")
    return request.form, request.files.getlist("style_image"), request.form.getlist("remove_style")


def project_info(db, p):
    refs = [r[0] for r in db.execute("SELECT id FROM style_refs WHERE project=? ORDER BY rowid", (p["id"],))]
    return {**dict(p), "style_refs": refs, "style_ref": refs[0] if refs else None}


def active_project(db, pid):
    p = row(db, "projects", pid)
    if p["archived"]:
        abort(409, "此项目已归档，可以在设置中恢复。")
    return p


def active_item(db, iid):
    item = row(db, "items", iid)
    active_project(db, item["project"])
    return item


def earlier_picture(db, pid, rank, vid):
    if not isinstance(vid, str):
        raise ValueError("请选择有效的分镜图片版本。")
    picture = db.execute("""SELECT v.id,v.ext,i.kind,i.rank,i.data,
        (SELECT count(*) FROM versions prev WHERE prev.item=v.item AND prev.view=v.view AND
         (prev.created,prev.rowid)<=(v.created,v.rowid)) AS number FROM versions v
        JOIN items i ON i.id=v.item WHERE v.id=? AND i.project=? AND i.kind='shot' AND v.view='分镜'""",
        (vid, pid)).fetchone()
    if picture is None or (rank is not None and picture["rank"] >= rank):
        raise ValueError("只能引用本项目中排在当前镜头之前的分镜图片。")
    return dict(picture)


def clean_item(db, pid, kind, data, rank=None):
    item = {"name": required(data.get("name"), 100), "brief": string(data.get("brief", ""))}
    if kind == "character":
        mode = data.get("referenceMode", "single")
        if mode not in ("single", "three"):
            raise ValueError("请选择单视角或三视图。")
        item["referenceMode"] = mode
    if kind != "shot":
        notes = data.get("viewNotes", {})
        if not isinstance(notes, dict) or any(view not in VIEWS[kind] for view in notes):
            raise ValueError("各视角补充要求应使用该素材支持的视角名称。")
        if notes:
            item["viewNotes"] = {view: string(note) for view, note in notes.items()}
        return item

    def check_ref(ident, asset_kind, view):
        if not isinstance(ident, str):
            raise ValueError("参考视角无效。")
        ref = row(db, "items", ident)
        if ref["project"] != pid or ref["kind"] != asset_kind:
            raise ValueError("只能选择当前项目的角色和场景。")
        if view is None and asset_kind == "character":
            view = "正面全身" if json.loads(ref["data"]).get("referenceMode") == "three" else "主图"
        if view not in [*VIEWS[asset_kind], "仅文字"]:
            raise ValueError("参考视角无效。")
        return {"id": ident, "view": view}

    cast = data.get("cast", [])
    if not isinstance(cast, list) or not all(isinstance(p, dict) for p in cast):
        raise ValueError("请选择有效的出场角色。")
    cast = [check_ref(p.get("id"), "character", p.get("view")) for p in cast]
    if len({p["id"] for p in cast}) != len(cast):
        raise ValueError("同一角色只能选择一次。")
    scene, view = data.get("scene", ""), data.get("sceneView", "主图")
    if scene:
        check_ref(scene, "scene", view)
    shot_refs = data.get("shotRefs", [])
    if not isinstance(shot_refs, list):
        raise ValueError("前序分镜参考必须是图片列表。")
    for vid in shot_refs:
        earlier_picture(db, pid, rank, vid)
    if len(set(shot_refs)) != len(shot_refs):
        raise ValueError("同一张分镜参考图只能添加一次。")
    notes = data.get("imageNotes", {})
    if not isinstance(notes, dict) or not all(re.fullmatch(r"[0-9a-f]{32}", key) for key in notes):
        raise ValueError("参考图说明格式无效。")
    item.update(camera=string(data.get("camera", "")), voice=string(data.get("voice", "")),
                cast=cast, scene=scene, sceneView=view if scene else "主图", shotRefs=shot_refs,
                imageNotes={vid:string(note) for vid,note in notes.items() if note != ""})
    return item


def generation_plan(db, root, item, project, form, upload, mask):
    kind = item["kind"]
    data = json.loads(item["data"])
    three = kind == "character" and data.get("referenceMode") == "three"
    view = form.get("view", "正面全身" if three else VIEWS[kind][0])
    if view not in VIEWS[kind]:
        raise ValueError("视角无效。")
    refs, roles, details = [], [], []
    notes = data.get("imageNotes", {})

    def describe(label, instruction, note=""):
        number = len(details) + 1
        details.append({"label":label, "note":note})
        roles.append(f"图{number}{instruction}" + (f" 图{number}补充说明：{note}" if note else ""))

    parent = form.get("parent", "")
    default_direction = "" if parent else data.get("viewNotes", {}).get(view, "")
    direction = string(form.get("direction", default_direction))
    repair_mode = None
    front_version = None
    if parent:
        source = row(db, "versions", parent)
        if source["item"] != item["id"] or upload is not None:
            raise ValueError("请使用当前素材的一个图片版本作为返修底图。")
        if not direction:
            raise ValueError("请填写修改要求。")
        view = source["view"]
        front_version = json.loads(source["meta"]).get("frontVersion")
        refs.append({**source, "kind":kind})
        if mask is None:
            repair_mode = "整图返修"
            describe("返修底图", "为需要修改的完整原图。请按修改要求编辑这张图，保留未要求改变的内容和构图。")
        else:
            repair_mode = "局部返修"
            describe("返修底图", "为原图的待修局部，保持像素布局、相机和光照，只修改指定内容。")
    else:
        if mask is not None:
            raise ValueError("请先选择返修底图。")
        if not data["brief"]:
            raise ValueError("请先保存角色设定、场景设定或镜头画面描述。")

    def reference(ident, angle):
        ref = row(db, "items", ident)
        if ref["project"] != project["id"]:
            raise ValueError("不能跨项目引用素材。")
        info = json.loads(ref["data"])
        label = f"{KIND[ref['kind']]}「{info['name']}」"
        if angle == "仅文字":
            roles.append(f"{label}设定：{info['brief']}")
            return
        selected = db.execute("SELECT * FROM versions WHERE item=? AND view=? AND adopted=1", (ident, angle)).fetchone()
        if not selected:
            raise ValueError(f"请先采用{label}的{angle}，或将该参考切换为「仅文字」。")
        refs.append({**dict(selected), "kind":ref["kind"]})
        describe(f"{info['name']} · {angle}", f"为{label}的{angle}。{info['brief']}", notes.get(selected["id"], ""))

    if not parent:
        if kind == "shot":
            if data.get("scene"):
                reference(data["scene"], data["sceneView"])
            for actor in data.get("cast", []):
                reference(actor["id"], actor["view"])
        elif three:
            if view not in THREE_VIEWS:
                raise ValueError("三视图支持正面、侧面和背面全身。")
            if view != "正面全身":
                front = db.execute("SELECT * FROM versions WHERE item=? AND view='正面全身' AND adopted=1", (item["id"],)).fetchone()
                if not front:
                    raise ValueError("请先生成并采用满意的正视图，再生成侧视图或背视图。")
                front_version = front["id"]
                if form.get("frontVersion", front_version) != front_version:
                    abort(409, "已采用的正视图发生变化，请刷新后确认参考版本。")
                refs.append({**dict(front), "kind":kind})
                describe("已采用的正视图", "为此角色已采用的正面全身图。依照它保持五官、身材比例、服装结构、配色和配饰佩戴侧，保持相同人物占幅与机位高度。")
            elif db.execute("SELECT 1 FROM versions WHERE item=? AND view='主图' AND adopted=1", (item["id"],)).fetchone():
                reference(item["id"], "主图")
        elif view != "主图":
            reference(item["id"], "主图")
    for vid in data.get("shotRefs", []):
        shot = earlier_picture(db, project["id"], item["rank"], vid)
        label = f"S{shot['rank']:02} · {json.loads(shot['data'])['name']} · V{shot['number']:02}"
        refs.append(shot)
        describe(label, f"为前序分镜「{label}」的参考画面。本镜的动作与机位以当前设定为准。", notes.get(vid, ""))
    styles = project_info(db, project)["style_refs"]
    for index, sid in enumerate(styles, 1):
        path = media_file(root, project["folder"], "style", sid, "png")
        refs.append({"id":sid, "image":path.read_bytes()})
        describe(f"项目画风参考图 {index}", "为项目画风参考，仅取用色彩、光影、笔触与材质。画中人物、道具和地点不带入本图。", notes.get(sid, ""))
    if upload is not None:
        describe("本次上传参考图", "是本次补充参考。", string(form.get("uploadNote", "")))
    instructions = {
        "character": ("绘制单人角色参考图，清楚呈现人物面貌、发型、体型、服装及配色。"
                      "默认使用与人物轮廓有区分度的中性纯色底，无环境布景、其他人物或无关道具。"
                      "角色设定或本次要求明确指定背景时，按指定背景绘制。"
                      + ("主图取三分之四正面全身。" if view == "主图" else f"按「{view}」取景。")),
        "scene": "绘制独立场景设定图，保持地标、布局、距离和材质，主图为空景建立镜头。",
        "shot": "绘制一张连贯的故事分镜，人物身份各自对应参考，保持场景空间关系。以本镜动作和机位为准。",
    }
    framing = "保持底图画幅。" if parent else f"画幅 {project['ratio']}。"
    prompt = "\n".join(filter(None, [*roles, f"{framing}统一画风：{project['style']}",
              repair_mode or instructions[kind], f"{data['name']} · {view}。{data['brief']}",
              THREE_VIEWS[view] if three and not parent else "",
              data.get("camera"), direction, "输出单张完整画面，不拼贴，不添加字幕或水印。"]))
    return view, refs, {"prompt": prompt, "refs": [r["id"] for r in refs], "ratio": project["ratio"],
                        "refDetails": details, "style_refs": styles, "frontVersion":front_version, "parent": parent, "repair_mode": repair_mode,
                        "supplement": upload is not None}


def create_app(root):
    app = Flask(__name__, static_folder=str(HERE / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 36 * 1024 * 1024
    secret = root / "service.dpapi"

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
            abort(403, "仅允许本机访问。")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("Origin") != request.host_url.rstrip("/"):
            abort(403, "请从本地工作台提交操作。")

    @app.after_request
    def protect(res):
        res.headers["X-Content-Type-Options"] = "nosniff"
        res.headers["Referrer-Policy"] = "no-referrer"
        res.headers["X-Frame-Options"] = "DENY"
        if request.path.startswith("/api/"):
            res.headers["Cache-Control"] = "no-store"
        return res

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(service="framebook", ready=True, api_version=API_VERSION)

    @app.route("/api/config", methods=["GET", "POST"])
    def config():
        conf = read_config(secret)
        if request.method == "POST":
            data = request.get_json()
            base = service_base(required(data.get("base"), 1000))
            model = required(data.get("model"), 160)
            key = string(data.get("key", ""), 4096) or (conf.get("key", "") if base == conf.get("base") else "")
            if not key or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ValueError("首次设置或更换服务地址时，请填写有效的 API Key。")
            text_model = string(data.get("textModel", conf.get("textModel", "")), 160)
            conf = {"base": base, "model": model, "key": key, "textModel": text_model}
            save_config(secret, conf)
        return jsonify(base=conf.get("base", ""), model=conf.get("model", "gpt-image-2.5-flare"),
                       textModel=conf.get("textModel", ""), configured=bool(conf))

    @app.get("/api/projects")
    def projects():
        with database(root) as db:
            result = []
            archived = [dict(p) for p in db.execute("SELECT id,name,rev FROM projects WHERE archived=1 ORDER BY modified DESC")]
            for p in db.execute("SELECT * FROM projects WHERE archived=0 ORDER BY modified DESC"):
                assets = list(db.execute("""SELECT i.id,i.kind,i.rank,i.data,v.id AS picture FROM items i
                    LEFT JOIN versions v ON v.item=i.id AND v.adopted=1 AND v.view=CASE
                    WHEN i.kind='shot' THEN '分镜' WHEN i.kind='character' AND json_extract(i.data,'$.referenceMode')='three'
                    THEN '正面全身' ELSE '主图' END
                    WHERE i.project=? ORDER BY i.rank""", (p["id"],)))
                shots = [a for a in assets if a["kind"] == "shot"]
                previews = [{"id": a["id"], "name": json.loads(a["data"])["name"], "picture": a["picture"], "rank": a["rank"]} for a in shots]
                covers = [a["picture"] for a in shots if a["picture"]] or [a["picture"] for a in assets if a["picture"]]
                result.append({**project_info(db, p), "shots": len(shots), "approved": sum(bool(a["picture"]) for a in shots),
                    "actors": sum(a["kind"] == "character" for a in assets), "covers": covers[:5], "previews": previews[:6]})
        return jsonify(projects=result, archived=archived, views=VIEWS, style=STYLE, api_version=API_VERSION)

    @app.post("/api/projects/import")
    def import_project():
        form = request.get_json()
        if not isinstance(form, dict) or form.get("mode", "draft") not in ("draft", "story"):
            raise ValueError("请选择映序导入稿或故事原文。")
        mode, text = form.get("mode", "draft"), read_text(form.get("text"))
        try:
            token = UUID(str(form["request"]))
        except (KeyError, ValueError):
            raise ValueError("导入请求无效，请重新打开导入窗口。") from None
        # A retry from the same dialog returns the project created by its first submission.
        pid = uuid5(NAMESPACE_URL, f"framebook.import/{token}/{mode}/{text}").hex

        def imported(db):
            return jsonify(id=pid, counts={r["kind"]: r["n"] for r in db.execute(
                "SELECT kind,count(*) AS n FROM items WHERE project=? GROUP BY kind", (pid,))})

        with database(root) as db:
            if db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                return imported(db)
        draft_text = split_story(text, read_config(secret)) if mode == "story" else text
        try:
            draft = parse_draft(draft_text)
            project_fields = clean_project(draft["project"])
            with database(root, True) as db:
                if db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    return imported(db)
                add_project(db, *project_fields, pid=pid)
                import_items(db, pid, draft, clean_item)
                return imported(db), 201
        except ValueError as exc:
            if mode == "story":
                return jsonify(error=f"拆镜稿需要修改。{exc}", draft=draft_text), 400
            raise

    @app.post("/api/projects")
    @app.patch("/api/projects/<pid>")
    def save_project(pid=None):
        creating = pid is None
        data, uploads, removals = project_form()
        fields = clean_project(data)
        with ExitStack() as rollback:
            with database(root, True) as db:
                if creating:
                    pid = add_project(db, *fields)
                p = active_project(db, pid)
                if not creating:
                    if str(p["rev"]) != str(data.get("rev")):
                        abort(409, "项目已在其他页面修改，请刷新后重试。")
                    db.execute("UPDATE projects SET name=?,style=?,ratio=?,modified=?,rev=rev+1 WHERE id=?",
                               (*fields, time.time(), pid))
                for sid in removals:
                    if not db.execute("DELETE FROM style_refs WHERE id=? AND project=?", (sid,pid)).rowcount:
                        raise ValueError("找不到当前项目的画风参考图。")
                    queue_removal(db, Path(p["folder"]) / MEDIA_KINDS["style"], sid, "png")
                for upload in uploads:
                    if not upload.filename:
                        continue
                    sid = uid()
                    path = media_file(root, p["folder"], "style", sid, "png")
                    with load_image(upload.stream) as image:
                        write_picture(path, image)
                    for file in (path, path.with_suffix(".webp")):
                        rollback.callback(file.unlink, missing_ok=True)
                    db.execute("INSERT INTO style_refs VALUES(?,?)", (sid, pid))
                result = None if creating else project_detail(db, pid)
            rollback.pop_all()
        if removals:
            cleanup_deleted_media(root)
        return (jsonify(id=pid), 201) if creating else jsonify(result)

    def project_detail(db, pid):
        p = active_project(db, pid)
        items = [serialize_item(db, i) for i in db.execute("SELECT * FROM items WHERE project=? ORDER BY rank", (pid,))]
        return {"project":project_info(db, p), "items":items}

    @app.post("/api/projects/<pid>/manage")
    def manage_project(pid):
        data = request.get_json()
        with database(root, True) as db:
            p = row(db, "projects", pid)
            if p["rev"] != data.get("rev"):
                abort(409, "项目已更新，请刷新后重试。")
            action = data.get("action")
            if action == "rename" and not p["archived"]:
                db.execute("UPDATE projects SET name=?,rev=rev+1,modified=? WHERE id=?", (required(data.get("name"),80), time.time(), pid))
            elif (action == "archive" and not p["archived"]) or (action == "restore" and p["archived"]):
                db.execute("UPDATE projects SET archived=?,rev=rev+1,modified=? WHERE id=?", (int(action=="archive"), time.time(), pid))
            else:
                raise ValueError("当前项目无法执行此操作。")
        return jsonify(ok=True)

    @app.route("/api/projects/<pid>", methods=["GET", "DELETE"])
    def project(pid):
        if request.method == "DELETE":
            with database(root, True) as db:
                p = row(db, "projects", pid)
                if not p["archived"]:
                    abort(409, "请先在项目库归档此项目。")
                delete_project(db, p)
            cleanup_deleted_media(root)
            return jsonify(ok=True)
        with database(root) as db:
            return jsonify(project_detail(db, pid))

    @app.post("/api/projects/<pid>/items")
    def new_item(pid):
        data = request.get_json()
        kind = data.get("kind")
        if kind not in KIND:
            raise ValueError("对象类型无效。")
        with database(root, True) as db:
            active_project(db, pid)
            ident = add_item(db, pid, kind, clean_item(db, pid, kind, data))
        return jsonify(id=ident), 201

    @app.patch("/api/items/<iid>")
    def save_item(iid):
        data = request.get_json()
        with database(root, True) as db:
            item = active_item(db, iid)
            if data.get("rev") != item["rev"]:
                abort(409, "此条目已在其他页面更新，请刷新后再编辑。")
            previous = json.loads(item["data"])
            for key in ("shotRefs", "imageNotes", "referenceMode", "viewNotes"):
                if key in previous:
                    data.setdefault(key, previous[key])
            clean = clean_item(db, item["project"], item["kind"], data, item["rank"])
            db.execute("UPDATE items SET data=?,rev=rev+1 WHERE id=?", (encode(clean), iid))
            touch(db, item["project"])
        return jsonify(ok=True, fields=clean)

    @app.post("/api/items/<iid>/move")
    def move(iid):
        delta = request.get_json().get("delta")
        if delta not in (-1, 1):
            raise ValueError("移动方向无效。")
        with database(root, True) as db:
            item = active_item(db, iid)
            other = db.execute("SELECT id,data FROM items WHERE project=? AND kind=? AND rank=?",
                               (item["project"], item["kind"], item["rank"]+delta)).fetchone()
            if other:
                if item["kind"] == "shot":
                    earlier, later = (item, other) if delta == 1 else (other, item)
                    selected = set(json.loads(later["data"]).get("shotRefs", []))
                    if selected and any(v[0] in selected for v in db.execute("SELECT id FROM versions WHERE item=?", (earlier["id"],))):
                        name = json.loads(later["data"])["name"]
                        abort(409, f"「{name}」引用了相邻的前序分镜。请先在「{name}」移除对应参考图，再调整这两个镜头的顺序。")
                db.executemany("UPDATE items SET rank=? WHERE id=?",
                               [(item["rank"]+delta, iid), (item["rank"], other["id"])])
                touch(db, item["project"])
        return jsonify(ok=True)

    @app.post("/api/versions/<vid>/adopt")
    def adopt(vid):
        with database(root, True) as db:
            v = row(db, "versions", vid)
            item = active_item(db, v["item"])
            db.execute("UPDATE versions SET adopted=0 WHERE item=? AND view=?", (v["item"], v["view"]))
            db.execute("UPDATE versions SET adopted=1 WHERE id=?", (vid,))
            touch(db, item["project"])
        return jsonify(ok=True)

    @app.get("/media/<vid>")
    def media(vid):
        thumbnail = "thumb" in request.args
        with database(root) as db:
            media = db.execute("""SELECT p.folder,'style' AS kind,'png' AS ext FROM style_refs s
                JOIN projects p ON p.id=s.project WHERE s.id=?
                UNION ALL SELECT p.folder,i.kind,v.ext FROM versions v JOIN items i ON i.id=v.item
                JOIN projects p ON p.id=i.project WHERE v.id=?""", (vid,vid)).fetchone()
            if not media:
                abort(404, "找不到所选图片。")
            ext = "webp" if thumbnail else media["ext"]
            path = media_file(root, media["folder"], media["kind"], vid, ext)
            return send_file(path, mimetype="image/jpeg" if ext=="jpg" else f"image/{ext}", max_age=31536000)

    @app.post("/api/items/<iid>/upload")
    def upload(iid):
        picture = load_image(request.files["file"].stream)
        with database(root, True) as db:
            item = active_item(db, iid)
            view = request.form.get("view")
            if view not in VIEWS[item["kind"]]:
                raise ValueError("视角无效。")
            vid = version(db, root, iid, view, picture, "本地导入", {})
            touch(db, item["project"])
        return jsonify(id=vid)

    def run_job(jid, iid, project, view, config, references, meta, supplement, mask):
        try:
            images = [load_image(io.BytesIO(r["image"]) if "image" in r else media_file(root, project["folder"], r["kind"], r["id"], r["ext"]))
                      for r in references]
            if supplement is not None:
                images.append(supplement)
            result = render(config, meta["prompt"], images, meta["ratio"], mask, keep_source_size=bool(meta["parent"]))
            with database(root, True) as db:
                if not db.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
                    return
                vid = version(db, root, iid, view, result, meta["repair_mode"] or "API 生成", meta)
                db.execute("UPDATE jobs SET state='done',version=? WHERE id=?", (vid, jid))
                touch(db, project["id"])
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else "图片处理未完成。请检查磁盘和服务商记录。"
            with database(root, True) as db:
                db.execute("UPDATE jobs SET state='error',error=? WHERE id=?", (message[:500], jid))

    @app.post("/api/items/<iid>/generate")
    def generate(iid):
        jid = UUID(request.form.get("request", "")).hex
        config = read_config(secret)
        if not config:
            raise ValueError("请先在 API 设置里保存服务商、模型和密钥。")
        upload = load_image(request.files["reference"].stream) if "reference" in request.files else None
        mask = load_image(request.files["mask"].stream, "L") if "mask" in request.files else None
        with database(root, True) as db:
            item = row(db, "items", iid)
            existing = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if existing:
                if existing["item"] != iid:
                    abort(409)
                return jsonify(id=jid), 202
            if db.execute("SELECT 1 FROM jobs WHERE item=? AND state='running'", (iid,)).fetchone():
                abort(409, "此条目正在生成，请等待当前任务完成。")
            p = active_project(db, item["project"])
            if str(item["rev"]) != request.form.get("rev"):
                abort(409, "条目已发生变化，请刷新后重试。")
            view, refs, meta = generation_plan(db, root, item, p, request.form, upload, mask)
            if mask is not None:
                first = refs[0]
                original = load_image(media_file(root, p["folder"], item["kind"], first["id"], first["ext"]))
                if original.size != mask.size or not mask.getbbox():
                    raise ValueError("遮罩为空或尺寸不匹配。")
            meta["model"] = config["model"]
            db.execute("INSERT INTO jobs VALUES(?,?, 'running','',?,'',?)", (jid, iid, view, time.time()))
        threading.Thread(target=run_job, args=(jid,iid,p,view,config,refs,meta,upload,mask), daemon=True).start()
        return jsonify(id=jid), 202

    @app.get("/api/jobs/<jid>")
    def job(jid):
        with database(root) as db:
            return jsonify(row(db, "jobs", jid))

    @app.get("/api/projects/<pid>/export")
    def export(pid):
        buf = io.BytesIO()
        with database(root) as db, zipfile.ZipFile(buf, "w") as archive:
            p = row(db, "projects", pid)
            shots = list(db.execute("""SELECT i.rank,i.data,v.id,v.ext FROM items i LEFT JOIN versions v
                   ON v.item=i.id AND v.adopted=1 WHERE i.project=? AND i.kind='shot' ORDER BY i.rank""", (pid,)))
            manifest = []
            for shot in shots:
                data = json.loads(shot["data"])
                name = re.sub(r'[^\w-]+', "_", data["name"])[:60]
                filename = f"{shot['rank']:03}_{name}.{shot['ext']}" if shot["id"] else ""
                if filename:
                    archive.write(media_file(root, p["folder"], "shot", shot["id"], shot["ext"]), filename)
                cast = [{**actor, "name": json.loads(row(db, "items", actor["id"])["data"])["name"]} for actor in data.get("cast", [])]
                scene = json.loads(row(db, "items", data["scene"])["data"])["name"] if data.get("scene") else ""
                earlier = []
                for vid in data.get("shotRefs", []):
                    ref = earlier_picture(db, pid, shot["rank"], vid)
                    earlier.append({"id":vid, "shot":ref["rank"], "name":json.loads(ref["data"])["name"],
                                    "version":ref["number"], "note":data.get("imageNotes", {}).get(vid, "")})
                manifest.append({**data, "shotRefs":earlier, "cast": cast, "sceneName": scene, "number": shot["rank"], "file": filename})
            archive.writestr("分镜清单.json", json.dumps({"project": p["name"], "ratio": p["ratio"], "shots": manifest}, ensure_ascii=False, indent=2).encode())
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name="分镜成片.zip", mimetype="application/zip")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    options = parser.parse_args()
    root = Path(os.environ.get("FRAMEBOOK_DATA", DEFAULT_ROOT))
    address = f"http://127.0.0.1:{options.port}"
    if options.open:
        try:
            with urlopen(address + "/api/health", timeout=1) as response:
                health = json.load(response)
                if health.get("service") == "framebook":
                    if health.get("api_version") != API_VERSION:
                        raise SystemExit("当前端口仍在运行旧版映序。请关闭旧服务窗口后再次启动映序。")
                    webbrowser.open(address)
                    raise SystemExit(0)
        except (URLError, ValueError, OSError):
            pass
    app = create_app(root)
    server = make_server("127.0.0.1", options.port, app, threaded=True)
    initialize(root, HERE.parent / "7704878630211565938")
    if options.open:
        webbrowser.open(address)
    server.serve_forever()
