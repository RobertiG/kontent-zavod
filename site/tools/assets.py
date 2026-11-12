"""Готовит картинки и видео сайта из исходников студии.

Исходники не меняются: читаем из папки студии на Рабочем столе, пишем копии в site/assets/.
Запуск из корня репозитория:
    uv run --no-project --with opencv-python-headless --with numpy --with pillow python site/tools/assets.py [шаг ...]
Шаги: qh17 qh17top slide neighbors box manual hd7000 works rich video  (без аргументов — все).
Картинка assets/img/box-closed-900.webp — снимок собранной 3D-коробки с самого сайта (для режима без движения).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageEnhance

SRC = Path("/Users/boss/Desktop/УК В Китае/контент-завод")
WORKS = SRC / "примеры-работ"
DESIGN = SRC / "образцы-оформления"
SOUNDBARS = SRC / "исходники-больших-саундбаров"
SITE = Path(__file__).resolve().parents[1]
IMG = SITE / "assets" / "img"
VID = SITE / "assets" / "video"
TMP = Path(tempfile.gettempdir()) / "kz-site-assets"
TMP.mkdir(exist_ok=True)


def webp(img: Image.Image, name: str, widths, q=78, folder=IMG):
    """Сохраняет картинку в нескольких ширинах: name-<w>.webp."""
    folder.mkdir(parents=True, exist_ok=True)
    for w in widths:
        im = img
        if img.width > w:
            h = round(img.height * w / img.width)
            im = img.resize((w, h), Image.LANCZOS)
        out = folder / f"{name}-{w}.webp"
        im.save(out, "WEBP", quality=q, method=6)
        print(f"  {out.relative_to(SITE)}  {im.width}x{im.height}  {out.stat().st_size // 1024} КБ")


def pdf_images(pdf: Path, first=None, last=None):
    out = TMP / pdf.stem
    out.mkdir(exist_ok=True)
    if not any(out.iterdir()):
        cmd = ["pdfimages", "-png"]
        if first:
            cmd += ["-f", str(first), "-l", str(last or first)]
        subprocess.run(cmd + [str(pdf), str(out / "im")], check=True)
    return sorted(out.glob("im-*.png"))


def pdf_page(pdf: Path, page: int, dpi: int) -> Image.Image:
    out = TMP / f"{pdf.stem}-p{page}-{dpi}"
    png = Path(str(out) + ".png")
    if not png.exists():
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-singlefile", "-png", str(pdf), str(out)], check=True)
    return Image.open(png).convert("RGB")


# ---------- QH-17: вырезанный товар на прозрачном фоне ----------
def cutout_dark_product(rgb: np.ndarray) -> np.ndarray:
    """Маска тёмного товара на светлом фоне: всё, что не связано с краем кадра светлым фоном."""
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    lum = (r * 299 + g * 587 + b * 114) // 1000
    green = g - np.maximum(r, b)
    product = ((lum < 150) | (green > 45)).astype(np.uint8)
    product = cv2.morphologyEx(product, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    # фон — то, что связано с рамкой кадра
    inv = (1 - product).astype(np.uint8)
    h, w = inv.shape
    ff = inv.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if ff[y, x] == 1:
            cv2.floodFill(ff, mask, (x, y), 2)
    alpha = np.where(ff == 2, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.2)
    return alpha


def step_qh17():
    print("QH-17: вырезка")
    ims = pdf_images(DESIGN / "Паспорт товара — QH-17 — 2026-11-09.pdf")
    src = np.array(Image.open(ims[0]).convert("RGB"))  # фронт на светлом фоне
    alpha = cutout_dark_product(src)
    ys, xs = np.where(alpha > 10)
    pad = 12
    y0, y1, x0, x1 = max(ys.min() - pad, 0), ys.max() + pad, max(xs.min() - pad, 0), xs.max() + pad
    rgba = np.dstack([src, alpha])[y0:y1, x0:x1]
    img = Image.fromarray(rgba, "RGBA")
    print(f"  товар: {img.width}x{img.height}, соотношение {img.width / img.height:.2f}")
    webp(img, "qh17-cut", [1400, 900, 560], q=82)
    # для ленты соседей и коробки — тот же товар на белом
    white = Image.new("RGB", img.size, "white")
    white.paste(img, mask=img.split()[3])
    white.save(TMP / "qh17-on-white.png")


# ---------- Слайд «Свежесть»: настоящий и сцена без надписей ----------
def step_slide():
    print("Слайд QH-17")
    path = WORKS / "2026-10-26-инфографика" / "photo_637933_0.jpg"
    img = Image.open(path).convert("RGB")
    webp(img, "qh17-slide", [1086, 720, 420], q=80)
    a = np.array(img)
    h, w = a.shape[:2]
    lum = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    mask = np.zeros((h, w), np.uint8)
    # заголовок и подзаголовок: светлые буквы на тёмной стене
    head = (slice(60, 350), slice(55, 1030))
    mask[head] = (lum[head] > 120).astype(np.uint8) * 255
    mask[head] = cv2.dilate(mask[head], np.ones((11, 11), np.uint8))
    # плашки целиком (скруглённые прямоугольники)
    for x0, x1 in [(52, 556), (560, 1042)]:
        cv2.rectangle(mask, (x0, 1262), (x1, 1362), 255, -1)
    clean = cv2.inpaint(a, mask, 12, cv2.INPAINT_TELEA)
    # мягкая зернистость, чтобы залатанные места не были «пластиковыми»
    noise = np.random.default_rng(7).normal(0, 5, a.shape)
    m3 = (cv2.GaussianBlur(mask, (0, 0), 6) / 255.0)[..., None]
    clean = np.clip(clean + noise * m3, 0, 255).astype(np.uint8)
    webp(Image.fromarray(clean), "qh17-scene", [1086, 720], q=80)


# ---------- Соседи в выдаче: тот же товар с фото фабрики ----------
def tile_crop(img: Image.Image, zoom=1.0, cx=0.5, cy=0.5, size=(480, 640)):
    """Кадрирует под плитку 3:4: zoom>1 — крупнее."""
    tw, th = size
    ratio = tw / th
    w, h = img.size
    if w / h > ratio:
        ch = h / zoom
        cw = ch * ratio
    else:
        cw = w / zoom
        ch = cw / ratio
    x = min(max(cx * w - cw / 2, 0), w - cw)
    y = min(max(cy * h - ch / 2, 0), h - ch)
    return img.crop((round(x), round(y), round(x + cw), round(y + ch))).resize(size, Image.LANCZOS)


def fit_tile(img: Image.Image, fill=0.86, cy=0.5, size=(480, 640)):
    """Вписывает широкое фото фабрики в плитку 3:4: фон продлевается краями кадра, товар целиком."""
    tw, th = size
    a = np.array(img)
    h, w = a.shape[:2]
    scale = tw * fill / w
    nw, nh = round(w * scale), round(h * scale)
    small = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_AREA)
    top = max(round(th * cy - nh / 2), 0)
    bottom = max(th - nh - top, 0)
    left = (tw - nw) // 2
    right = tw - nw - left
    big = cv2.copyMakeBorder(small, top, bottom, left, right, cv2.BORDER_REPLICATE)[:th, :tw]
    soft = cv2.GaussianBlur(big, (0, 0), 18)
    m = np.zeros((th, tw), np.float32)
    m[top:top + nh, left:left + nw] = 1
    m = cv2.GaussianBlur(m, (0, 0), 6)[..., None]
    out = (big * m + soft * (1 - m)).astype(np.uint8)
    return Image.fromarray(out)


def tint(img, warm=0.0, bright=1.0, sat=1.0):
    img = ImageEnhance.Brightness(img).enhance(bright)
    img = ImageEnhance.Color(img).enhance(sat)
    if warm:
        r, g, b = img.split()
        r = r.point(lambda v: min(255, v + warm * 12))
        b = b.point(lambda v: max(0, v - warm * 12))
        img = Image.merge("RGB", (r, g, b))
    return img


def step_neighbors():
    print("Соседи")
    pas = [Image.open(p).convert("RGB") for p in pdf_images(DESIGN / "Паспорт товара — QH-17 — 2026-11-09.pdf")]
    box = [Image.open(p).convert("RGB") for p in pdf_images(DESIGN / "Коробка — QH-17 — 2026-11-10.pdf")]
    front, salmon, kit = pas
    vertical = max(box, key=lambda im: im.width * im.height)
    on_white = Image.open(TMP / "qh17-on-white.png").convert("RGB")
    on_white = ImageOps.expand(on_white, border=40, fill="white")
    variants = [
        tile_crop(vertical, 1.0),
        fit_tile(front, 0.92, 0.5),
        tint(fit_tile(kit, 0.98, 0.5), warm=0.6),
        tint(tile_crop(vertical, 1.7, 0.5, 0.47), bright=1.03),
        ImageOps.mirror(tint(tile_crop(vertical, 1.25, 0.5, 0.5), bright=1.05)),
        fit_tile(on_white, 0.9, 0.46),
        ImageOps.mirror(tint(fit_tile(front, 0.8, 0.56), warm=-0.5, bright=1.04)),
        tint(fit_tile(kit, 0.9, 0.52), sat=0.85),
        ImageOps.mirror(tint(fit_tile(kit, 1.0, 0.47), warm=0.3, bright=1.04)),
    ]
    for i, im in enumerate(variants, 1):
        webp(im, f"feed-{i}", [360], q=72)


# ---------- Коробка: грани из развёртки QH-17 ----------
def step_box():
    print("Коробка")
    pdf = DESIGN / "Коробка — QH-17 — 2026-11-10.pdf"
    dpi = 150
    page = pdf_page(pdf, 1, dpi)
    s = dpi / 72
    H = 2264.655  # высота страницы в pt (из SVG-разбора)

    def crop(x0, x1, y0, y1, inset=2):
        box_ = (round(x0 * s) + inset, round((H - y1) * s) + inset, round(x1 * s) - inset, round((H - y0) * s) - inset)
        return page.crop(box_)

    lid = crop(346.02, 1564.91, 1326.62, 1873.71)        # 430×193 мм: фото товара, «QH-17»
    wall = crop(346.02, 1564.91, 1873.71, 2188.35)       # 430×111 мм: логотип, «Вакуумный упаковщик»
    side_r = crop(1564.91, 1879.56, 1326.62, 1873.71)    # 111×193 мм: характеристики
    side_l = crop(31.37, 346.02, 1326.62, 1873.71)       # 111×193 мм: изготовитель, импортёр
    # на сайте закрываем блок изготовителя/импортёра и штрихкод: там реквизиты
    sl = np.array(side_l)
    red = np.median(sl[int(sl.shape[0] * 0.9):, :], axis=(0, 1)).astype(np.uint8)
    y_a, y_b = int(sl.shape[0] * 0.17), int(sl.shape[0] * 0.98)
    sl[y_a:y_b, :] = red
    for i, yy in enumerate(range(y_a + 30, y_b - 40, 34)):
        x_end = int(sl.shape[1] * (0.82 if i % 3 else 0.55))
        cv2.rectangle(sl, (24, yy), (x_end, yy + 7), (255, 214, 214), -1)
    side_l = Image.fromarray(sl)
    webp(lid, "box-lid", [900, 560], q=80)
    webp(wall, "box-wall", [900, 560], q=80)
    webp(side_r, "box-side-r", [360], q=80)
    webp(side_l, "box-side-l", [360], q=80)
    # штрихкод для дна — из развёртки
    bars = [p for p in pdf_images(pdf) if Image.open(p).size == (226, 100)]
    if bars:
        Image.open(bars[0]).convert("RGB").save(IMG / "box-barcode.png", optimize=True)


# ---------- Инструкция: страницы 1–7 (8–9 — реквизиты и QR, не показываем) ----------
def step_manual():
    print("Инструкция")
    pdf = DESIGN / "Инструкция — QH-17 — 2026-11-10.pdf"
    for p in range(1, 8):
        page = pdf_page(pdf, p, 150)
        webp(page, f"manual-{p}", [620, 320], q=78)


# ---------- Саундбар HD7000: кадры на белом + страница паспорта ----------
def step_hd7000():
    print("HD7000")
    d = SOUNDBARS / "HD7000"
    for name in ["01_front", "04_angle45", "02_side", "03_back", "06_details_collage", "05_scale", "07_kit"]:
        img = Image.open(d / f"{name}.jpg").convert("RGB")
        webp(img, f"hd7000-{name.split('_', 1)[1].replace('_collage', '')}", [900, 480], q=80)
    page = pdf_page(d / "Паспорт_HD7000.pdf", 1, 110)
    webp(page, "hd7000-passport", [760, 420], q=80)


# ---------- Работы студии по товарам ----------
WORK_SETS = {
    "vacuum": ("2026-10-26-инфографика", range(637931, 637939)),
    "sharpener": ("2026-10-26-инфографика", range(637925, 637931)),
    "knife": ("2026-10-23-главное-фото-и-воронка", range(637627, 637635)),
    "airfryer": ("2026-10-28-генерация-без-участия", range(638182, 638191)),
    "humidifier": ("2026-10-26-автоанализ-конкурентов", range(637977, 637985)),
    "speakers": ("2026-11-05-codex", range(639431, 639439)),
    "scissors": ("2026-11-06-deepseek-и-codex", range(639507, 639514)),
    "soundbar": ("2026-10-21-rich", range(637184, 637193)),
}


def step_works():
    print("Работы")
    for key, (folder, ids) in WORK_SETS.items():
        for i, n in enumerate(ids, 1):
            img = Image.open(WORKS / folder / f"photo_{n}_0.jpg").convert("RGB")
            webp(img, f"{key}-{i}", [600, 300], q=76, folder=IMG / "works")


def step_rich():
    print("RICH")
    for i, n in enumerate(range(637178, 637184), 1):
        img = Image.open(WORKS / "2026-10-21-rich" / f"photo_{n}_0.jpg").convert("RGB")
        img = img.resize((1774, 887), Image.LANCZOS) if img.width != 1774 else img
        webp(img, f"rich-{i}", [760], q=78)
    for i, n in enumerate(range(637635, 637640), 1):
        img = Image.open(WORKS / "2026-10-23-главное-фото-и-воронка" / f"photo_{n}_0.jpg").convert("RGB")
        webp(img, f"rich-knife-{i}", [760], q=78)


# ---------- Видео: без звука, H.264, постер ----------
def ffmpeg(src: Path, out: Path, scale: str, crf: int):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an", "-vf", f"scale={scale}:flags=lanczos,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-profile:v", "high", "-movflags", "+faststart", str(out)], check=True)
    poster = TMP / (out.stem + ".png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-frames:v", "1", str(poster)], check=True)
    webp(Image.open(poster).convert("RGB"), out.stem + "-poster", [Image.open(poster).width], q=76, folder=VID)
    print(f"  {out.relative_to(SITE)}  {out.stat().st_size // 1024} КБ")


def step_video():
    print("Видео")
    ffmpeg(WORKS / "2026-11-06-видеообложки" / "Видеообложка_Т16_5s_3x4.mp4", VID / "cover-t16.mp4", "540:720", 27)
    ffmpeg(WORKS / "2026-11-06-видеообложки" / "Видеообложка_LT-K79_5s.mp4", VID / "cover-lt-k79.mp4", "540:960", 27)
    ffmpeg(WORKS / "2026-11-05-codex" / "BLACKRED_A22_30s.mp4", VID / "reel-a22.mp4", "720:1280", 28)


def step_qh17top():
    """Товар сверху под 45° (из фото комплекта) — для сцены упаковки: так камера смотрит в коробку."""
    print("QH-17: вид сверху")
    ims = pdf_images(DESIGN / "Паспорт товара — QH-17 — 2026-11-09.pdf")
    src = np.array(Image.open(ims[2]).convert("RGB"))
    r, g, b = [src[..., i].astype(int) for i in range(3)]
    lum = (r * 299 + g * 587 + b * 114) // 1000
    m = ((lum < 120) | ((g - np.maximum(r, b)) > 45)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    big = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    dev = (lab == big).astype(np.uint8)
    # дыры внутри корпуса (блики стекла) заливаем
    ff = (1 - dev).copy()
    h, w = ff.shape
    cv2.floodFill(ff, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 2)
    alpha = np.where(ff == 2, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.4)
    ys, xs = np.where(alpha > 10)
    rgba = np.dstack([src, alpha])[ys.min() - 10:ys.max() + 10, xs.min() - 10:xs.max() + 10]
    img = Image.fromarray(rgba, "RGBA").rotate(-15.5, resample=Image.BICUBIC, expand=True)
    img = img.crop(img.getbbox())
    print(f"  {img.width}x{img.height}, соотношение {img.width / img.height:.2f}")
    webp(img, "qh17-top", [900, 560], q=82)


STEPS = {
    "qh17": step_qh17, "qh17top": step_qh17top, "slide": step_slide, "neighbors": step_neighbors, "box": step_box, "manual": step_manual,
    "hd7000": step_hd7000, "works": step_works, "rich": step_rich, "video": step_video,
}

if __name__ == "__main__":
    for name in sys.argv[1:] or STEPS:
        STEPS[name]()
