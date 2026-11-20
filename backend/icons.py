"""图标绘制：应用图标（打包用）+ 托盘动态图标（显示当前账号剩余百分比）。"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

COLORS = {
    "ok": (18, 161, 80),
    "low": (231, 154, 11),
    "out": (229, 72, 77),
    "none": (47, 107, 255),
}
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size):
    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def _center_text(draw, box, text, font, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    x = box[0] + (box[2] - box[0] - w) / 2 - l
    y = box[1] + (box[3] - box[1] - h) / 2 - t
    draw.text((x, y), text, font=font, fill=fill)


def app_icon(size=256):
    """蓝色圆角方块 + 白色 C + 右下角绿色状态点。"""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 竖向渐变底
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    top, bottom = (66, 126, 255), (31, 79, 214)
    for y in range(s):
        k = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    _center_text(d, (0, -s * 0.03, s * 0.94, s), "C", _font(int(s * 0.72)), (255, 255, 255, 255))
    r = s * 0.13
    cx, cy = s * 0.76, s * 0.74
    d.ellipse([cx - r - s * 0.03, cy - r - s * 0.03, cx + r + s * 0.03, cy + r + s * 0.03], fill=(31, 79, 214, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(46, 204, 113, 255))
    return img


def tray_icon(remaining=None, level="none", size=64):
    """托盘图标：颜色表示状态，数字表示当前账号剩余百分比。"""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=COLORS.get(level, COLORS["none"]) + (255,))
    if remaining is None:
        text, fsize = "C", int(s * 0.78)
    else:
        text = str(int(remaining))
        fsize = int(s * (0.62 if len(text) <= 2 else 0.46))
    _center_text(d, (0, 0, s, s), text, _font(fsize), (255, 255, 255, 255))
    return img


def mac_app_icon(size=512):
    """符合 macOS HIG 留白规范的应用图标（~80.5% 主体居中 + ~9.8% 四周透明留白 + 柔和阴影）。"""
    s = size
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    body = round(s * 824 / 1024)
    pad = (s - body) // 2

    # 柔和底部投影，与原生 macOS 程序坞图标视觉统一
    shadow_mask = Image.new("L", (s, s), 0)
    y_off = max(1, round(s * 0.012))
    ImageDraw.Draw(shadow_mask).rounded_rectangle(
        [pad, pad + y_off, pad + body - 1, pad + body - 1 + y_off],
        radius=int(body * 0.225),
        fill=64,
    )
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=s * 0.016))
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    shadow.putalpha(shadow_mask)
    canvas = Image.alpha_composite(canvas, shadow)

    # 2x 超采样绘制主体再抗锯齿缩放
    inner = app_icon(body * 2).resize((body, body), Image.LANCZOS)
    fg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    fg.paste(inner, (pad, pad), inner)
    return Image.alpha_composite(canvas, fg)


def write_app_icons(folder):
    os.makedirs(folder, exist_ok=True)
    big = app_icon(256)
    big.save(os.path.join(folder, "icon.png"))
    big.save(os.path.join(folder, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    mac_app_icon(512).save(os.path.join(folder, "icon_mac.png"))


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    write_app_icons(os.path.join(here, "assets"))
    tray_icon(77, "ok").save(os.path.join(here, "assets", "_tray_preview.png"))
    print("icons written")
