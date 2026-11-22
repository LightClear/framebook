"""Transport and image processing for the standalone Windows workbench."""
import base64
import io
import re
from urllib.parse import urlsplit, urlunsplit

import requests
from PIL import Image, ImageOps

RATIOS = {"3:4": (3, 4), "9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}
VIEWS = {"character": ["主图", "正面全身", "侧面全身", "背面全身", "脸部近景"],
         "scene": ["主图", "反打机位", "地标近景", "同机位夜景"], "shot": ["分镜"]}
THREE_VIEWS = {"正面全身": "正面直立，面部和躯干正对镜头，双脚完整入画。",
               "侧面全身": "同一角色转为严格侧面，面向画面左侧，完整展示从头到脚的侧面轮廓。",
               "背面全身": "同一角色背对镜头直立，完整展示后脑、背部服装和鞋跟。"}


def service_base(value):
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path.rstrip("/").endswith(("/edits", "/generations"))):
        raise ValueError("请填写 HTTPS 服务前缀，例如 https://api.example.com/v1")
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/") or "/v1", "", ""))


def load_image(stream, mode="RGB"):
    try:
        with Image.open(stream) as im:
            if im.width * im.height > 32_000_000:
                raise ValueError("图片超过 3200 万像素，请缩小后再试。")
            return ImageOps.exif_transpose(im).convert(mode)
    except (OSError, Image.DecompressionBombError):
        raise ValueError("无法读取图片，请使用有效的 PNG、JPEG 或 WebP。") from None


def png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def request_image(config, prompt, refs):
    route = "edits" if refs else "generations"
    fields = {"model": config["model"], "prompt": prompt, "size": "auto", "n": 1}
    if refs:
        field = "image" if len(refs) == 1 else "image[]"
        body = {"data": fields, "files": [(field, (f"ref{i}.png", png_bytes(im), "image/png"))
                                         for i, im in enumerate(refs, 1)]}
    else:
        body = {"json": fields}
    try:
        res = requests.post(f"{config['base']}/images/{route}", headers={"Authorization": "Bearer " + config["key"]},
                            timeout=(15, 600), allow_redirects=False, **body)
        if not res.ok or res.is_redirect:
            try:
                error = res.json().get("error", {})
                detail = error.get("message", "") if isinstance(error, dict) else str(error)
            except (ValueError, AttributeError):
                detail = ""
            detail = re.sub(r"(?i)(?:bearer\s+|sk-)[\w.\-]+", "[隐藏]", str(detail).replace(config["key"], "[隐藏]"))
            raise ValueError(f"服务商返回 HTTP {res.status_code}（{config['model']}）。{detail[:240]}")
        data = res.json()["data"][0]
        if data.get("b64_json"):
            raw = base64.b64decode(data["b64_json"], validate=True)
        elif urlsplit(data.get("url", "")).scheme == "https":
            with requests.get(data["url"], timeout=(15, 120)) as download:
                download.raise_for_status()
                raw = download.content
        else:
            raise ValueError("服务商没有返回图片数据或 HTTPS 图片链接。")
        return load_image(io.BytesIO(raw))
    except requests.RequestException:
        raise ValueError("未收到完整的服务商响应。请先核对服务商记录，再决定是否重新生成。") from None
    except (KeyError, IndexError, TypeError):
        raise ValueError("服务商响应缺少有效的 data 图片。") from None


def render(config, prompt, refs, ratio, mask=None, *, keep_source_size=False):
    if mask is None:
        output = request_image(config, prompt, refs)
        if keep_source_size:
            return ImageOps.fit(output, refs[0].size)
        w, h = RATIOS[ratio]
        unit = min(output.width // w, output.height // h)
        if not unit:
            raise ValueError("服务商返回的图像尺寸过小。")
        return ImageOps.fit(output, (w * unit, h * unit))
    original = refs[0].copy()
    if original.size != mask.size or not mask.getbbox():
        raise ValueError("请在所选版本上涂出要修改的区域。")
    l, t, r, b = mask.getbbox()
    pad = max(48, max(r - l, b - t) // 3)
    box = (max(0, l-pad), max(0, t-pad), min(original.width, r+pad), min(original.height, b+pad))
    crop = original.crop(box)
    side = max(crop.size)
    offset = ((side-crop.width)//2, (side-crop.height)//2)
    square = Image.new("RGB", (side, side), crop.getpixel((0, 0)))
    square.paste(crop, offset)
    edited = ImageOps.fit(request_image(config, prompt, [square, *refs[1:]]), (side, side))
    x, y = offset
    original.paste(edited.crop((x, y, x+crop.width, y+crop.height)), box, mask.crop(box))
    return original
