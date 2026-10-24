"""Анимацияли стикерлар (WEBM VP9, 512px, 3 с, 30 fps) ва эмодзи (100px) — расмий логотип + оддий шакллар."""
import math, os, subprocess, shutil, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
G = (62, 100, 60, 255); C = (240, 228, 213, 255); W = (255, 255, 255, 255); GOLD = (201, 162, 39, 255)
B = "/mnt/user-data/uploads/d/LumbertureHub/data/brand/"
EM = Image.open(B + "logo_emblem.png").convert("RGBA")
VT = Image.open(B + "logo_vertical.png").convert("RGBA")
F = "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf"
N, FPS = 90, 30


def fit(im, box):
    r = min(box[0] / im.width, box[1] / im.height)
    return im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)


def recolor(im, col):
    """Белгини бир рангга бўяш (шакли ўзгармайди)."""
    o = Image.new("RGBA", im.size, col); o.putalpha(im.split()[3]); return o


EM_G, EM_C = EM, recolor(EM, C)
ease = lambda t: 1 - (1 - t) ** 3
def back(t):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def outline(img, px=8):
    a = img.split()[3].filter(ImageFilter.MaxFilter(px * 2 + 1))
    o = Image.new("RGBA", img.size, (0, 0, 0, 0)); o.paste(W, (0, 0), a); o.alpha_composite(img); return o


def text_block(d, lines, maxw, size0=66):
    size = size0
    while True:
        f = ImageFont.truetype(F, size)
        bb = [d.textbbox((0, 0), l, font=f) for l in lines]
        if max(b[2] - b[0] for b in bb) <= maxw or size < 28:
            return f, bb, int(size * 1.15)
        size -= 2


def paste_scaled(dst, im, cx, cy, s, rot=0):
    if s <= 0.01:
        return
    w, h = max(1, int(im.width * s)), max(1, int(im.height * s))
    x = im.resize((w, h), Image.LANCZOS)
    if rot:
        x = x.rotate(rot, resample=Image.BICUBIC, expand=True)
    dst.alpha_composite(x, (int(cx - x.width / 2), int(cy - x.height / 2)))


def card(bg, H, y0, scale=1.0):
    s = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
    w = 472 * scale; h = H * scale; cx, cy = 256, y0 + H / 2
    d.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=56 * scale, fill=bg)
    return s


# --- иконкалар (оддий шакллар) ---
def ic_check(d, cx, cy, r, prog, col):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=12)
    pts = [(cx - r * .45, cy + r * .02), (cx - r * .1, cy + r * .38), (cx + r * .5, cy - r * .35)]
    L1 = math.dist(pts[0], pts[1]); L2 = math.dist(pts[1], pts[2]); L = (L1 + L2) * prog
    if L <= 0: return
    if L <= L1:
        e = (pts[0][0] + (pts[1][0] - pts[0][0]) * L / L1, pts[0][1] + (pts[1][1] - pts[0][1]) * L / L1); d.line([pts[0], e], fill=col, width=16, joint="curve")
    else:
        k = (L - L1) / L2; e = (pts[1][0] + (pts[2][0] - pts[1][0]) * k, pts[1][1] + (pts[2][1] - pts[1][1]) * k)
        d.line([pts[0], pts[1], e], fill=col, width=16, joint="curve")


def ic_heart(d, cx, cy, r, col):
    pts = []
    for i in range(60):
        t = math.pi * 2 * i / 60
        x = 16 * math.sin(t) ** 3; y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * r / 17, cy - y * r / 17))
    d.polygon(pts, fill=col)


def ic_box(d, cx, cy, r, col, lid=0):
    d.rectangle((cx - r, cy - r * .55, cx + r, cy + r * .75), fill=col)
    ly = cy - r * .55 - lid
    d.rectangle((cx - r * 1.12, ly - r * .32, cx + r * 1.12, ly), fill=col)
    d.rectangle((cx - r * .14, cy - r * .55, cx + r * .14, cy + r * .75), fill=GOLD)


def ic_coin(d, cx, cy, r, phase, col, fg):
    w = r * abs(math.cos(phase)); w = max(w, 4)
    d.ellipse((cx - w, cy - r, cx + w, cy + r), fill=col)
    if w > r * .45 and r > 8:
        f = ImageFont.truetype(F, int(r * 1.1)); bb = d.textbbox((0, 0), "$", font=f)
        tw = (bb[2] - bb[0]) * w / r
        d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), "$", font=f, fill=fg)


def sparkle(d, cx, cy, r, col):
    d.polygon([(cx, cy - r), (cx + r * .25, cy - r * .25), (cx + r, cy), (cx + r * .25, cy + r * .25), (cx, cy + r),
               (cx - r * .25, cy + r * .25), (cx - r, cy), (cx - r * .25, cy - r * .25)], fill=col)


def phrase_sticker(name, lines, icon, dark):
    if os.path.exists(f"{OUT}/{name}.webm"): print(name, "exists"); return
    bg, fg = (G, C) if dark else (C, G)
    for i in range(N):
        t = i / N
        appear = ease(min(1, i / 12))
        H = 330; y0 = (512 - H) / 2
        s = card(bg, H, y0, 0.6 + 0.4 * back(min(1, i / 14)))
        d = ImageDraw.Draw(s)
        icx, icy, ir = 256, y0 + 92, 58
        if icon == "heart":
            pul = 1 + 0.12 * math.sin(t * math.pi * 6) ** 2
            ic_heart(d, icx, icy, ir * pul * appear, fg)
        elif icon == "check":
            ic_check(d, icx, icy, ir * appear, ease(max(0, min(1, (i - 10) / 25))), fg)
        elif icon == "box":
            jump = abs(math.sin(t * math.pi * 3)) * 18
            ic_box(d, icx, icy - jump + 6, ir * .8 * appear, fg, lid=jump * .6)
            for k, (dx, dy) in enumerate(((-110, -40), (110, -55), (-95, 30), (100, 35))):
                ph = (t * 3 + k * .25) % 1
                sparkle(d, icx + dx, icy + dy, 16 * math.sin(ph * math.pi), GOLD)
        elif icon == "coin":
            ic_coin(d, icx, icy, ir * appear, t * math.pi * 4, GOLD, bg)
        elif icon == "sun":
            ic_sun(d, icx, icy, ir * .62 * appear, t, GOLD)
        elif icon == "loupe":
            ic_loupe(d, icx, icy, ir * appear, t, fg)
        elif icon == "phone":
            ic_phone(d, icx, icy, ir * appear, t, fg, bg)
        elif icon == "logo":
            pz = appear * (1 + 0.05 * math.sin(t * math.pi * 4))
            if dark:                                     # логотип рангини ўзгартирмаймиз: крем доира ичида яшил белги
                rr = 72 * pz; d.ellipse((icx - rr, icy - rr, icx + rr, icy + rr), fill=C)
                paste_scaled(s, fit(EM_G, (84, 96)), icx, icy, pz)
            else:
                paste_scaled(s, fit(EM_G, (110, 125)), icx, icy, pz)
        f, bb, lh = text_block(d, lines, 400)
        ty = y0 + 175 + (H - 175 - lh * len(lines)) / 2 - 8
        k = ease(max(0, min(1, (i - 6) / 14)))
        for j, (l, b) in enumerate(zip(lines, bb)):
            tl = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); td = ImageDraw.Draw(tl)
            td.text(((512 - (b[2] - b[0])) / 2 - b[0], ty + j * lh - b[1] + (1 - k) * 30), l, font=f, fill=fg)
            a = tl.split()[3].point(lambda v: int(v * k)); tl.putalpha(a); s.alpha_composite(tl)
        outline(s).save(f"{OUT}/_f{i:03d}.png")
    enc(name, 512)


def logo_sticker(name):
    v = fit(VT, (300, 430))
    for i in range(N):
        t = i / N
        s = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
        sc = 0.5 + 0.5 * back(min(1, i / 16)); bob = math.sin(t * math.pi * 2) * 8
        w, h = (v.width + 60) * sc, (v.height + 40) * sc
        d.rounded_rectangle((256 - w / 2, 256 - h / 2 + bob, 256 + w / 2, 256 + h / 2 + bob), radius=60 * sc, fill=C)
        lay = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); paste_scaled(lay, v, 256, 256 + bob, sc)
        # ялтироқ чизиқ (фақат логотип ичида)
        sh = Image.new("L", (512, 512), 0); sd = ImageDraw.Draw(sh)
        xx = -200 + ((t * 1.4) % 1) * 900
        sd.polygon([(xx, 0), (xx + 60, 0), (xx - 140, 512), (xx - 200, 512)], fill=110)
        hl = Image.new("RGBA", (512, 512), (255, 255, 255, 0)); hl.putalpha(Image.composite(sh, Image.new("L", (512, 512), 0), lay.split()[3]))
        s.alpha_composite(lay); s.alpha_composite(hl)
        outline(s).save(f"{OUT}/_f{i:03d}.png")
    enc(name, 512)


def emoji_anim(name, circle):
    for i in range(N):
        t = i / N
        s = Image.new("RGBA", (100, 100), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
        p = 1 + 0.07 * math.sin(t * math.pi * 4)
        if circle:
            r = 49 * p; d.ellipse((50 - r, 50 - r, 50 + r, 50 + r), fill=C)
            paste_scaled(s, fit(EM, (58, 64)), 50, 50, p)
        else:
            paste_scaled(s, fit(EM, (88, 92)), 50, 50 + math.sin(t * math.pi * 2) * 3, 1)
        s.save(f"{OUT}/_f{i:03d}.png")
    enc(name, 100)


def enc(name, size):
    for crf in (30, 36, 42, 48):
        out = f"{OUT}/{name}.webm"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{OUT}/_f%03d.png", "-c:v", "libvpx-vp9",
                        "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf), "-an", "-t", "3", out], check=True)
        lim = 64 * 1024 if size == 100 else 256 * 1024
        if os.path.getsize(out) <= lim:
            break
    for f in os.listdir(OUT):
        if f.startswith("_f"):
            os.remove(os.path.join(OUT, f))
    print(name, os.path.getsize(out))



def ic_clock(d, cx, cy, r, t, col):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=12)
    a = t * math.pi * 4 - math.pi / 2
    d.line([(cx, cy), (cx + r * .62 * math.cos(a), cy + r * .62 * math.sin(a))], fill=col, width=11)
    d.line([(cx, cy), (cx, cy - r * .42)], fill=col, width=11)
    d.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=col)


def ic_gear(d, cx, cy, r, t, col, bg):
    rot = t * math.pi * 2 / 3
    pts = []
    for k in range(16):
        a = rot + k * math.pi / 8
        rr = r if k % 2 == 0 else r * .78
        for da in (-0.13, 0.13):
            pts.append((cx + rr * math.cos(a + da), cy + rr * math.sin(a + da)))
    d.polygon(pts, fill=col)
    d.ellipse((cx - r * .34, cy - r * .34, cx + r * .34, cy + r * .34), fill=bg)


def ic_truck(d, cx, cy, r, t, col, bg):
    dx = math.sin(t * math.pi * 2) * 10
    x0 = cx - r * 1.2 + dx
    d.rounded_rectangle((x0, cy - r * .6, x0 + r * 1.45, cy + r * .45), radius=8, fill=col)
    d.polygon([(x0 + r * 1.5, cy - r * .3), (x0 + r * 2.05, cy - r * .3), (x0 + r * 2.35, cy + r * .05), (x0 + r * 2.35, cy + r * .45), (x0 + r * 1.5, cy + r * .45)], fill=col)
    for wx in (x0 + r * .4, x0 + r * 1.95):
        d.ellipse((wx - r * .28, cy + r * .3, wx + r * .28, cy + r * .86), fill=col)
        d.ellipse((wx - r * .11, cy + r * .47, wx + r * .11, cy + r * .69), fill=bg)
    for k in range(3):
        ph = (t * 3 + k / 3) % 1
        y = cy - r * .35 + k * r * .3
        d.line([(x0 - 18 - ph * 40, y), (x0 - 18 - ph * 40 - 26, y)], fill=GOLD, width=7)


def ic_star(d, cx, cy, r, t, col):
    p = 1 + 0.1 * math.sin(t * math.pi * 6)
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r * p if k % 2 == 0 else r * .45 * p
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=col)
    for k, (dx, dy) in enumerate(((-105, -30), (108, -45), (-90, 40), (95, 38))):
        ph = (t * 3 + k * .25) % 1
        sparkle(d, cx + dx, cy + dy, 14 * math.sin(ph * math.pi), GOLD)


def phrase2(name, lines, icon, dark):
    if os.path.exists(f"{OUT}/{name}.webm"): print(name, "exists"); return
    bg, fg = (G, C) if dark else (C, G)
    for i in range(N):
        t = i / N
        appear = ease(min(1, i / 12))
        H = 330; y0 = (512 - H) / 2
        s = card(bg, H, y0, 0.6 + 0.4 * back(min(1, i / 14)))
        d = ImageDraw.Draw(s)
        icx, icy, ir = 256, y0 + 92, 58
        if icon == "heart":
            pul = 1 + 0.12 * math.sin(t * math.pi * 6) ** 2
            ic_heart(d, icx - 26, icy, ir * .72 * pul * appear, fg); ic_heart(d, icx + 30, icy - 4, ir * .55 * pul * appear, GOLD)
        elif icon == "check":
            ic_check(d, icx, icy, ir * appear, ease(max(0, min(1, (i - 10) / 25))), fg)
        elif icon == "clock":
            ic_clock(d, icx, icy, ir * appear, t, fg)
        elif icon == "gear":
            ic_gear(d, icx, icy, ir * appear, t, fg, bg)
        elif icon == "truck":
            ic_truck(d, icx - 10, icy - 6, ir * .62 * appear, t, fg, bg)
        elif icon == "star":
            ic_star(d, icx, icy, ir * appear, t, fg)
        elif icon == "sun":
            ic_sun(d, icx, icy, ir * .62 * appear, t, GOLD)
        elif icon == "loupe":
            ic_loupe(d, icx, icy, ir * appear, t, fg)
        elif icon == "phone":
            ic_phone(d, icx, icy, ir * appear, t, fg, bg)
        elif icon == "logo":
            pz = appear * (1 + 0.05 * math.sin(t * math.pi * 4))
            if dark:
                rr = 72 * pz; d.ellipse((icx - rr, icy - rr, icx + rr, icy + rr), fill=C)
                paste_scaled(s, fit(EM_G, (84, 96)), icx, icy, pz)
            else:
                paste_scaled(s, fit(EM_G, (110, 125)), icx, icy, pz)
        f, bb, lh = text_block(d, lines, 400)
        ty = y0 + 175 + (H - 175 - lh * len(lines)) / 2 - 8
        k = ease(max(0, min(1, (i - 6) / 14)))
        for j, (l, b) in enumerate(zip(lines, bb)):
            tl = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); td = ImageDraw.Draw(tl)
            td.text(((512 - (b[2] - b[0])) / 2 - b[0], ty + j * lh - b[1] + (1 - k) * 30), l, font=f, fill=fg)
            a = tl.split()[3].point(lambda v: int(v * k)); tl.putalpha(a); s.alpha_composite(tl)
        outline(s).save(f"{OUT}/_f{i:03d}.png")
    enc(name, 512)



def ic_sun(d, cx, cy, r, t, col):
    if r < 14:
        return
    rot = t * math.pi / 2
    for k in range(12):
        a = rot + k * math.pi / 6
        d.line([(cx + r * 1.25 * math.cos(a), cy + r * 1.25 * math.sin(a)), (cx + r * 1.65 * math.cos(a), cy + r * 1.65 * math.sin(a))], fill=col, width=9)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)


def ic_loupe(d, cx, cy, r, t, col):
    if r < 14:
        return
    dx = math.sin(t * math.pi * 4) * 12
    c = (cx - r * .15 + dx, cy - r * .15)
    rr = r * .62
    d.ellipse((c[0] - rr, c[1] - rr, c[0] + rr, c[1] + rr), outline=col, width=12)
    d.line([(c[0] + rr * .7, c[1] + rr * .7), (c[0] + rr * 1.55, c[1] + rr * 1.55)], fill=col, width=18)


def ic_phone(d, cx, cy, r, t, col, bg):
    if r < 30:
        return
    sh = math.sin(t * math.pi * 12) * 5 if (t * 3) % 1 < .4 else 0
    w, h = r * .82, r * 1.45
    d.rounded_rectangle((cx - w / 2 + sh, cy - h / 2, cx + w / 2 + sh, cy + h / 2), radius=14, fill=col)
    d.rounded_rectangle((cx - w / 2 + 8 + sh, cy - h / 2 + 14, cx + w / 2 - 8 + sh, cy + h / 2 - 22), radius=6, fill=bg)
    for k in range(2):
        ph = (t * 2 + k * .5) % 1
        rr = r * (.9 + ph * .7)
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), -40, 40, fill=GOLD, width=6)
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), 140, 220, fill=GOLD, width=6)


ITEMS = [("c1", ["Хайрли тонг!"], "sun", False), ("c2", ["Доброе утро!"], "sun", True),
         ("c3", ["Хайрли кун!"], "sun", True), ("c4", ["Хорошего дня!"], "sun", False),
         ("c5", ["Табриклаймиз!"], "star", True), ("c6", ["Поздравляем!"], "star", False),
         ("c7", ["Байрамингиз", "муборак!"], "star", False), ("c8", ["С праздником!"], "star", True),
         ("c9", ["Тушундим!"], "check", False), ("c10", ["Понял!"], "check", True),
         ("c11", ["Ҳозир", "текшираман"], "loupe", True), ("c12", ["Сейчас", "проверю"], "loupe", False),
         ("c13", ["Қўнғироқ", "қилинг"], "phone", False), ("c14", ["Позвоните", "нам"], "phone", True),
         ("c15", ["Кечирасиз!"], "heart", True), ("c16", ["Извините!"], "heart", False)]
for it in ITEMS:
    phrase2(*it)
