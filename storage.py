import json
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from PIL import Image

STYLE = "现场纪实摄影，略带手持感的构图。低饱和灰绿与泥土棕，真实的皮肤、织物和金属纹理，自然漫射光。"
MEDIA_KINDS = {"style": "画风", "character": "角色", "scene": "场景", "shot": "分镜"}


def uid():
    return uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def database(root, write=False):
    db = sqlite3.connect(root / "library.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    finally:
        db.close()


def initialize(root, example=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "media").mkdir(exist_ok=True)
    with database(root) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, style TEXT,
                ratio TEXT, source TEXT, modified REAL, rev INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, project TEXT REFERENCES projects(id),
                kind TEXT, rank INTEGER, data TEXT, rev INTEGER DEFAULT 1);
            CREATE INDEX IF NOT EXISTS item_order ON items(project,kind,rank);
            CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, item TEXT REFERENCES items(id),
                view TEXT, ext TEXT, adopted INTEGER, origin TEXT, meta TEXT, created REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_adopted ON versions(item,view) WHERE adopted=1;
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, item TEXT REFERENCES items(id), state TEXT,
                error TEXT, view TEXT, version TEXT, created REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON jobs(item) WHERE state='running';
        """)
    with database(root, True) as db:
        old_version = db.execute("PRAGMA user_version").fetchone()[0]
        if old_version < 2:
            migrate_media(db, root)
        db.execute("UPDATE jobs SET state='error',error=? WHERE state='running'",
                   ("工作台已重启，请核对服务商记录后决定是否重新生成。",))
        if old_version == 0:
            if example and (example / "analysis/manual_visual_review.json").is_file() and not db.execute(
                    "SELECT 1 FROM projects LIMIT 1").fetchone():
                seed(db, root, example)
        db.execute("PRAGMA user_version=2")
    cleanup_deleted_media(root)


def project_folder(name, pid):
    label = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "_", name).strip()[:32]
    return f"{label or '项目'}--{pid}"


def media_file(root, folder, kind, vid, ext):
    return root / "media" / folder / MEDIA_KINDS[kind] / f"{vid}.{ext}"


def queue_removal(db, directory, vid, ext):
    for suffix in (ext, "webp"):
        relative = Path(directory) / f"{vid}.{suffix}"
        db.execute("INSERT OR IGNORE INTO media_cleanup(path) VALUES(?)", (relative.as_posix(),))


def migrate_media(db, root):
    db.execute("ALTER TABLE projects ADD COLUMN created REAL")
    db.execute("ALTER TABLE projects ADD COLUMN created_estimated INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE projects ADD COLUMN folder TEXT")
    db.execute("CREATE TABLE style_refs (id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE)")
    db.execute("CREATE INDEX styles_by_project ON style_refs(project)")
    db.execute("CREATE TABLE media_cleanup (path TEXT PRIMARY KEY)")
    old_tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "deleted_media" in old_tables:
        for vid, ext in db.execute("SELECT id,ext FROM deleted_media"):
            queue_removal(db, "", vid, ext)
        db.execute("DROP TABLE deleted_media")
    for p in db.execute("SELECT id,name,modified FROM projects").fetchall():
        pid = p["id"]
        folder = project_folder(p["name"], pid)
        created = db.execute("""SELECT min(t) FROM (
            SELECT ? AS t UNION ALL SELECT v.created FROM versions v JOIN items i ON i.id=v.item WHERE i.project=?
            UNION ALL SELECT j.created FROM jobs j JOIN items i ON i.id=j.item WHERE i.project=?)""",
            (p["modified"], pid, pid)).fetchone()[0]
        db.execute("UPDATE projects SET folder=?,created=?,created_estimated=1 WHERE id=?", (folder, created, pid))
        for v in db.execute("SELECT v.id,v.ext,i.kind FROM versions v JOIN items i ON i.id=v.item WHERE i.project=?", (pid,)):
            for ext in (v["ext"], "webp"):
                source = root / "media" / f"{v['id']}.{ext}"
                if source.is_file():
                    dest = media_file(root, folder, v["kind"], v["id"], ext)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
            queue_removal(db, "", v["id"], v["ext"])
        if "project_styles" in old_tables:
            for s in db.execute("SELECT id,image,thumb FROM project_styles WHERE project=?", (pid,)):
                for ext, blob in (("png",s["image"]), ("webp",s["thumb"])):
                    dest = media_file(root, folder, "style", s["id"], ext)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(blob)
                db.execute("INSERT INTO style_refs VALUES(?,?)", (s["id"], pid))
    if "project_styles" in old_tables:
        db.execute("DROP TABLE project_styles")


def delete_project(db, project):
    pid, folder = project["id"], project["folder"]
    items = "SELECT id FROM items WHERE project=?"
    for v in db.execute("SELECT v.id,v.ext,i.kind FROM versions v JOIN items i ON i.id=v.item WHERE i.project=?", (pid,)):
        queue_removal(db, Path(folder) / MEDIA_KINDS[v["kind"]], v["id"], v["ext"])
    for s in db.execute("SELECT id FROM style_refs WHERE project=?", (pid,)):
        queue_removal(db, Path(folder) / MEDIA_KINDS["style"], s["id"], "png")
    db.execute(f"DELETE FROM jobs WHERE item IN ({items})", (pid,))
    db.execute(f"DELETE FROM versions WHERE item IN ({items})", (pid,))
    db.execute("DELETE FROM items WHERE project=?", (pid,))
    db.execute("DELETE FROM projects WHERE id=?", (pid,))


def cleanup_deleted_media(root):
    # Commit the deletion before removing files. Retry any locked files on the next startup.
    with database(root, True) as db:
        base = (root / "media").resolve()
        for media in db.execute("SELECT path FROM media_cleanup").fetchall():
            path = (base / media["path"]).resolve()
            if not path.is_relative_to(base) or path == base:
                raise ValueError("图片清理路径超出素材目录。")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            for parent in path.parents:
                if parent == base:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
            db.execute("DELETE FROM media_cleanup WHERE path=?", (media["path"],))


def add_project(db, name, style, ratio, source="", *, pid=None):
    pid = pid or uid()
    now = time.time()
    db.execute("INSERT INTO projects(id,name,style,ratio,source,modified,created,folder) VALUES(?,?,?,?,?,?,?,?)",
               (pid, name, style, ratio, source, now, now, project_folder(name, pid)))
    return pid


def add_item(db, pid, kind, data):
    iid = uid()
    rank = db.execute("SELECT coalesce(max(rank),0)+1 FROM items WHERE project=? AND kind=?", (pid, kind)).fetchone()[0]
    db.execute("INSERT INTO items(id,project,kind,rank,data) VALUES(?,?,?,?,?)", (iid, pid, kind, rank, encode(data)))
    touch(db, pid)
    return iid


def touch(db, pid):
    db.execute("UPDATE projects SET modified=? WHERE id=?", (time.time(), pid))


def write_picture(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if isinstance(image, Path):
            shutil.copyfile(image, path)
        else:
            image.save(path, "PNG")
        with Image.open(path) as original:
            thumb = original.convert("RGB")
            thumb.thumbnail((760, 760))
            thumb.save(path.with_suffix(".webp"), "WEBP", quality=82)
    except Exception:
        path.unlink(missing_ok=True)
        path.with_suffix(".webp").unlink(missing_ok=True)
        raise


def version(db, root, iid, view, image, origin, meta, adopted=False):
    vid = uid()
    ext = "jpg" if isinstance(image, Path) else "png"
    folder, kind = db.execute("SELECT p.folder,i.kind FROM projects p JOIN items i ON i.project=p.id WHERE i.id=?", (iid,)).fetchone()
    path = media_file(root, folder, kind, vid, ext)
    write_picture(path, image)
    try:
        db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)",
                   (vid, iid, view, ext, int(adopted), origin, encode(meta), time.time()))
    except Exception:
        path.unlink(missing_ok=True)
        path.with_suffix(".webp").unlink(missing_ok=True)
        raise
    return vid


def seed(db, root, source):
    pid = add_project(db, "铁傀儡的村庄", STYLE, "3:4", "本地视频拆解 · 15 张原片参考")
    refs = source / "analysis/refs"
    assets = {}

    def asset(kind, name, description, file=None):
        iid = add_item(db, pid, kind, {"name": name, "brief": description})
        assets[name] = iid
        if file:
            version(db, root, iid, "主图", refs / file, "原片参考", {"source": file}, True)

    asset("character", "守卫", "灰白方形铁臂，带氧化斑和藤叶。镜头常从守卫的视线向下看。", "golem-arm.jpg")
    asset("character", "兜帽孩子", "棕色尖兜帽、宽松袖口，小个子。", "child.jpg")
    asset("character", "玩家", "短棕发、浓短须、青蓝上衣和银色胸甲。", "player-outfit.jpg")
    version(db, root, assets["玩家"], "脸部近景", refs / "player-face.jpg", "原片参考", {}, True)
    asset("scene", "村庄", "粗石与木梁村屋，泥土道路，农田和石井。", "village.jpg")
    asset("scene", "木栅隔间", "木栅、铁网和矿车轨道，远处仍能看见村庄。")
    asset("scene", "水流平台", "石墙围合的平台、水流、方形洞口与墙上的灯。")
    casts = [["守卫","兜帽孩子"],["守卫"],["玩家"],["玩家"],[],[],["玩家","守卫"],["守卫","玩家"],
             ["玩家","守卫"],["玩家"],["兜帽孩子","玩家"],["守卫"],["玩家","守卫"],[],["守卫"]]
    review = json.loads((source / "analysis/manual_visual_review.json").read_text(encoding="utf-8-sig"))
    for shot in review["shots"]:
        n = shot["n"]
        scene = "村庄" if n <= 9 or n == 13 else "木栅隔间" if n <= 12 else "水流平台" if n == 15 else ""
        data = {"name": shot["title"], "brief": shot["seen"], "camera": shot["camera"], "voice": "",
                "cast": [{"id": assets[c], "view": "主图"} for c in casts[n-1]],
                "scene": assets.get(scene, ""), "sceneView": "仅文字" if scene in {"木栅隔间","水流平台"} else "主图"}
        iid = add_item(db, pid, "shot", data)
        version(db, root, iid, "分镜", source / shot["file"], "原片参考", {"source": shot["file"]}, True)
