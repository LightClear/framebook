"""Read a Framebook project draft, optionally splitting a story with a text model."""
import json
from pathlib import Path

import requests

from imaging import THREE_VIEWS, VIEWS
from storage import add_item

HERE = Path(__file__).resolve().parent
FORMAT = "framebook.project.v1"
MAX_TEXT_BYTES = 4 * 1024 * 1024


def read_text(value):
    if not isinstance(value, str) or not (value := value.strip().lstrip("\ufeff").strip()):
        raise ValueError("请粘贴文本或选择文件。")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("文字含无效字符，请使用 UTF-8 编码。") from None
    if size > MAX_TEXT_BYTES:
        raise ValueError("文本超过 4 MiB，请缩短后重试。")
    return value


def split_story(text, config):
    model = config.get("textModel")
    if not model:
        raise ValueError("请先在设置中填写文本模型 ID，或用 Skill 生成映序导入稿。")
    guide = (HERE / "prompts" / "映序故事提示词.txt").read_text(encoding="utf-8").split("我的素材")[0]
    example = (HERE / "static" / "映序导入示例.json").read_text(encoding="utf-8")
    prompt = (guide + "\n本次使用映序导入稿模式。只返回一个符合以下示例结构的 JSON 对象。"
              "故事中的人物、地点、镜头和画风取自用户素材，示例只演示结构。"
              "三组数组按制作顺序列出，cast 和 scene 按同类唯一名称引用，独立地点填 null。"
              "角色 referenceMode 可用 single 或 three，三视图从正面全身开始。"
              "可在 viewNotes 按视角填写补充要求。画幅支持 3:4、9:16、16:9、1:1。"
              "每张分镜的 cast 和 scene 视角可选对应素材的视角或仅文字。"
              f"角色视角：{'、'.join(VIEWS['character'])}。场景视角：{'、'.join(VIEWS['scene'])}。"
              "\n" + example)
    try:
        response = requests.post(config["base"] + "/chat/completions",
            headers={"Authorization": "Bearer " + config["key"]}, allow_redirects=False, timeout=(15, 240),
            json={"model": model, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]})
    except requests.RequestException:
        raise ValueError("未收到文本模型的完整响应，请检查服务地址与连接后重试。") from None
    if response.status_code != 200:
        raise ValueError(f"文本服务返回 HTTP {response.status_code}，请核对服务地址、文本模型 ID 和密钥。")
    try:
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("文本模型输出达到长度上限，请缩短故事或减少镜数。")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("文本服务返回了空的拆镜结果。")
        return read_text(content)
    except (KeyError, IndexError, AttributeError, TypeError, requests.exceptions.JSONDecodeError):
        raise ValueError("文本服务未返回有效的故事拆镜结果。") from None


def unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"导入稿中字段「{key}」重复。")
        fields[key] = value
    return fields


def fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}应为 JSON 对象。")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{label}含不支持的字段「{sorted(unknown)[0]}」。")
    return value


def records(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label}应为数组，没有条目时请填写 []。")
    return value


def parse_draft(text):
    start_line = 0
    if not text.startswith("{"):
        lines = text.splitlines()
        fences = [(i, line.strip().lower()) for i, line in enumerate(lines) if line.lstrip().startswith("```")]
        if fences:
            if len(fences) != 2 or fences[0][1] not in ("```", "```json") or fences[1][1] != "```":
                raise ValueError("请只保留一个完整的 JSON 代码块。")
            start_line = fences[0][0] + 1
            text = "\n".join(lines[start_line:fences[1][0]])
    try:
        draft = json.loads(text, object_pairs_hook=unique_fields)
    except json.JSONDecodeError as exc:
        raise ValueError(f"导入稿第 {start_line + exc.lineno} 行第 {exc.colno} 列不是有效 JSON。"
                         "请使用 Skill 的「映序导入稿」输出，或切换到「故事原文」。") from None
    except RecursionError:
        raise ValueError("导入稿的 JSON 嵌套层数过多。") from None
    fields(draft, {"format", "project", "characters", "scenes", "shots"}, "导入稿")
    if draft.get("format") != FORMAT:
        raise ValueError(f"请选择 {FORMAT} 格式的映序导入稿。")
    fields(draft.get("project"), {"name", "style", "ratio"}, "项目")
    for key, label in (("characters", "角色"), ("scenes", "场景"), ("shots", "分镜")):
        records(draft.get(key), label)
    return draft


def named_reference(value, names, label):
    fields(value, {"name", "view"}, label)
    name = value.get("name")
    if not isinstance(name, str) or name.strip() not in names:
        raise ValueError(f"{label}引用了未声明的名称「{name}」。")
    iid, views = names[name.strip()]
    view = value.get("view")
    if view is not None and view not in [*views, "仅文字"]:
        raise ValueError(f"{label}「{name}」不支持视角「{view}」。")
    return {"id": iid, "view": view}


def import_items(db, pid, draft, clean_item):
    assets = {"character": {}, "scene": {}}
    sections = (("character", "characters", "角色", {"name", "brief", "referenceMode", "viewNotes"}),
                ("scene", "scenes", "场景", {"name", "brief", "viewNotes"}),
                ("shot", "shots", "分镜", {"name", "brief", "camera", "voice", "cast", "scene"}))
    for kind, key, section, allowed in sections:
        for index, entry in enumerate(draft[key], 1):
            label = f"{section}第 {index} 项"
            item = fields(entry, allowed, label).copy()
            try:
                if kind == "shot":
                    item["cast"] = [named_reference(ref, assets["character"], "出场角色")
                                    for ref in records(item.get("cast", []), "出场角色")]
                    if item.get("scene") is not None:
                        scene = named_reference(item["scene"], assets["scene"], "场景")
                        item.update(scene=scene["id"], sceneView=scene["view"] if scene["view"] is not None else "主图")
                    else:
                        item["scene"] = ""
                clean = clean_item(db, pid, kind, item)
                if kind == "shot":
                    add_item(db, pid, kind, clean)
                    continue
                if clean["name"] in assets[kind]:
                    raise ValueError(f"名称「{clean['name']}」重复，请使用同类内唯一的名称。")
                views = THREE_VIEWS if clean.get("referenceMode") == "three" else VIEWS[kind]
                if any(view not in views for view in clean.get("viewNotes", {})):
                    raise ValueError("三视图的补充要求仅支持正面全身、侧面全身和背面全身。")
                iid = add_item(db, pid, kind, clean)
                assets[kind][clean["name"]] = (iid, views)
            except ValueError as exc:
                raise ValueError(f"{label}：{exc}") from exc
