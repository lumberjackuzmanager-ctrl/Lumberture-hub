"""Тесты защиты: python tests/test_security.py (временная БД, сеть не используется)."""
import os, sys, io, re, time, tempfile
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "s.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
with A.app.app_context():                       # тестларда «cashier» — аввалгидек кенг роль (созламалардан ташқари ҳаммаси, проведен қилади)
    A.db().execute("UPDATE roles SET kind='full', sections='[]', can_post=1 WHERE key='cashier'"); A.db().commit()
    import roles as _roles_t; _roles_t.reload()
import security as sec
from werkzeug.security import generate_password_hash

def boom():
    raise RuntimeError("secret internal detail")
A.app.add_url_rule("/__boom", "boom", boom)          # тестовая ошибка (маршрут добавляется до первого запроса)
A.app.testing = True
A.app.config["SHOW_ERROR_PAGE"] = True
LAN, LAN2, OUT = "192.168.1.50", "192.168.1.60", "203.0.113.7"
assert not any(x in sec.own_ips() for x in (LAN, LAN2, OUT))

def client(ip="127.0.0.1"):
    cl_ = A.app.test_client(); cl_.environ_base["REMOTE_ADDR"] = ip
    return cl_
def tok(c):
    with c.session_transaction() as s:
        s.setdefault("csrf", "tok-" + str(id(c))); return s["csrf"]
def post(c, url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = tok(c); return c.post(url, data=d, **kw)
def login(c, u="admin", p="admin123"):
    return post(c, "/login", {"username": u, "password": p})
def con():
    return A.connect()

# ---- без отладки, заголовки, cookie
assert A.app.debug is False and A.app.config["PROPAGATE_EXCEPTIONS"] is False
c = client()
r = c.get("/login")
assert r.status_code == 200
for k, v in [("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")]:
    assert r.headers.get(k) == v, (k, r.headers.get(k))
csp = r.headers["Content-Security-Policy"]
assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
assert "Permissions-Policy" in r.headers
r = login(c)
assert r.status_code == 302, r.status_code
ck = next((h for h in r.headers.getlist("Set-Cookie") if h.startswith("rent_session=")), "")
assert "rent_session=" in ck and "HttpOnly" in ck and "SameSite=Lax" in ck, ck
assert c.get("/").headers.get("Cache-Control") == "no-store"
assert A.app.config["MAX_CONTENT_LENGTH"] == 5 * 1024 * 1024

# ---- CSRF: любой POST без токена или с чужим токеном -> 400 (в т.ч. вход)
c2 = client()
assert c2.post("/login", data={"username": "admin", "password": "admin123"}).status_code == 400
with c2.session_transaction() as s:
    s["csrf"] = "right"
assert c2.post("/login", data={"username": "admin", "password": "admin123", "csrf": "wrong"}).status_code == 400
n_post = 0
for rule in A.app.url_map.iter_rules():
    if rule.rule.startswith("/api/online/"):         # сайт API — кукисиз, Bearer токен (test_online.py)
        continue
    if "POST" in rule.methods and rule.endpoint not in ("static", "api_basis_import"):    # Базис скрипти — CSRF эмас, ўз калити (403 калитсиз)
        url = re.sub(r"<(?:int:)?[^>]+>", "1", rule.rule)
        r = c.post(url, data={"action": "delete", "id": "1"})                      # без csrf, но с входом админа
        assert r.status_code == 400, (url, r.status_code)
        assert "Форма устарела" in r.get_data(as_text=True)
        n_post += 1
assert c.post("/api/basis/import", json={"panels": [1]}).status_code == 403          # калитсиз — рад
assert c.post("/api/online/order", json={"v": 1}).status_code == 401                 # сайт API: токенсиз — рад (сессия кукиси ҳисобга олинмайди)
assert n_post >= 30, n_post
html = c.get("/admin/users").get_data(as_text=True)
assert html.count('name="csrf"') >= 1

# ---- ошибки: без трассировки пользователю, подробности — в data/errors.log
r = c.get("/__boom")
body = r.get_data(as_text=True)
assert r.status_code == 500 and "secret internal detail" not in body and "Traceback" not in body and "Ошибка программы" in body
with open(os.path.join(tmp, "errors.log"), encoding="utf-8") as fh:
    assert "secret internal detail" in fh.read()
assert c.get("/nope-404").status_code == 404

# ---- загрузка: больше 5 МБ -> 413 (понятное сообщение), не Excel -> отказ
big = io.BytesIO(b"x" * (5 * 1024 * 1024 + 10))
r = c.post("/admin/opening/import", data={"csrf": tok(c), "file": (big, "big.xlsx")}, content_type="multipart/form-data")
assert r.status_code == 413 and "Файл слишком большой" in r.get_data(as_text=True)
r = c.post("/admin/opening/import", data={"csrf": tok(c), "file": (io.BytesIO(b"{}"), "x.json")}, content_type="multipart/form-data",
           follow_redirects=True)
assert "Нужен файл Excel" in r.get_data(as_text=True)

# ---- хэши паролей (scrypt/pbkdf2) и стандартный пароль
with A.app.app_context():
    h = A.q("SELECT password_hash FROM users WHERE username='admin'", one=True)[0]
    assert sec.hash_ok(h) and sec.hash_method(h) in ("scrypt", "pbkdf2"), h
    assert not sec.hash_ok("pbkdf2:sha256:1000$x$y") and not sec.hash_ok("md5$x") and sec.hash_ok("pbkdf2:sha256:600000$x$y")
assert A.check_weak_passwords() >= 0
with A.app.app_context():
    assert A.weak_users() == ["admin"]
h = c.get("/").get_data(as_text=True)
assert "Стандартный пароль у: <b>admin</b>" in h

# ---- политика паролей при смене
for bad, msg in [("Ab1", "минимум 8"), ("abcdefgh", "буквы, и цифры"), ("12345678", "буквы, и цифры"), ("admin123", "слишком известный"),
                 ("kassir2026", "не должен содержать логин")]:
    r = post(c, "/admin/users", {"username": "kassir", "password": bad, "role": "cashier"}); 
    page = c.get("/admin/users").get_data(as_text=True)
    assert msg in page, (bad, msg)
    assert not con().execute("SELECT 1 FROM users WHERE username='kassir'").fetchone()
post(c, "/admin/users", {"username": "kassir", "password": "Tolov2026", "role": "cashier"})
assert con().execute("SELECT weak_pw FROM users WHERE username='kassir'").fetchone()[0] == 0
assert sec.password_problem("Rasul2026x") is None and sec.password_problem("пароль2026") is None

# ---- подбор пароля: 5 неудач (логин + IP) -> блок 15 мин., даже с верным паролем
A.connect().execute("UPDATE settings SET value=value").connection.close()
cb = client()
for i in range(5):
    r = login(cb, "admin", f"wrong{i}")
    assert r.status_code == 200 and "Неверный логин или пароль" in r.get_data(as_text=True)
r = login(cb, "admin", "admin123")
assert r.status_code == 429 and "заблокирован" in r.get_data(as_text=True)
k = con()
rows = k.execute("SELECT reason FROM login_attempts WHERE username='admin' AND ip='127.0.0.1' ORDER BY id DESC LIMIT 6").fetchall()
assert [x[0] for x in rows] == ["locked"] + ["bad"] * 5, rows
# другой логин с того же ПК не заблокирован
assert login(client(), "kassir", "Tolov2026").status_code == 302
# админ видит блокировку и журнал, разблокирует
h = c.get("/admin/security").get_data(as_text=True)
assert "Сейчас заблокированы" in h and "неверный пароль" in h and "заблокирован (подбор)" in h and "Доступ по IP" in h
post(c, "/admin/security", {"action": "unlock", "username": "admin", "ip": "127.0.0.1"})
assert login(client(), "admin", "admin123").status_code == 302
# через 15 минут блок снимается сам
for i in range(6):
    login(cb, "kassir", f"bad{i}")
assert login(cb, "kassir", "Tolov2026").status_code == 429
k.execute("UPDATE login_attempts SET ts=ts-16*60 WHERE username='kassir' AND ok=0"); k.commit()
assert login(cb, "kassir", "Tolov2026").status_code == 302
# несуществующий логин — тот же ответ, тоже считается
r = login(client(), "nobody", "x")
assert "Неверный логин или пароль" in r.get_data(as_text=True)
assert k.execute("SELECT reason FROM login_attempts ORDER BY id DESC LIMIT 1").fetchone()[0] == "nouser"

# ---- доступ по IP: по умолчанию только этот ПК
with A.app.app_context():
    assert A.get_setting("ip_allow") == sec.DEFAULT_IP_ALLOW
cl = client(LAN)
r = cl.get("/login")
assert r.status_code == 403 and "Доступ с этого компьютера запрещён" in r.get_data(as_text=True) and LAN in r.get_data(as_text=True)
assert client(OUT).get("/").status_code == 403 and client(OUT).get("/static/style.css").status_code == 403
assert client("::1").get("/login").status_code == 200 and client("::ffff:127.0.0.1").get("/login").status_code == 200
assert k.execute("SELECT COUNT(*) FROM login_attempts WHERE reason='ip' AND ip=?", (LAN,)).fetchone()[0] == 1
cl.get("/login")
assert k.execute("SELECT COUNT(*) FROM login_attempts WHERE reason='ip' AND ip=?", (LAN,)).fetchone()[0] == 1   # не чаще раза в 10 мин.
# ошибка в списке -> не сохраняется
post(c, "/admin/security", {"action": "ip", "ip_allow": "127.0.0.1\n192.168.1.300", "ip_admin_allow": ""})
assert "Не распознаны адреса: 192.168.1.300" in c.get("/admin/security").get_data(as_text=True)
# разрешили всю сеть офиса
post(c, "/admin/security", {"action": "ip", "ip_allow": "127.0.0.1\n192.168.1.0/24 # офис", "ip_admin_allow": ""})
with A.app.app_context():
    assert A.get_setting("ip_allow") == "127.0.0.1\n192.168.1.0/24 # офис"
assert cl.get("/login").status_code == 200 and client(OUT).get("/login").status_code == 403
# с компьютера сети нельзя сохранить список, который закроет доступ ему самому; с этого ПК — можно (он разрешён всегда)
ca = client(LAN); assert login(ca).status_code == 302
post(ca, "/admin/security", {"action": "ip", "ip_allow": "127.0.0.1", "ip_admin_allow": ""})
assert "потерял бы доступ" in ca.get("/admin/security").get_data(as_text=True)
with A.app.app_context():
    assert "192.168.1.0/24" in A.get_setting("ip_allow")
# администраторам — только с ПК владельца
post(c, "/admin/security", {"action": "ip", "ip_allow": "192.168.1.0/24", "ip_admin_allow": "192.168.1.22"})
assert ca.get("/").status_code == 403                                 # открытая сессия админа с другого ПК закрыта
r = login(client(LAN2), "admin", "admin123")
assert r.status_code in (403,) and k.execute("SELECT reason FROM login_attempts ORDER BY id DESC LIMIT 1").fetchone()[0] in ("ip-admin",)
ck_ = client(LAN2); assert login(ck_, "kassir", "Tolov2026").status_code == 302 and ck_.get("/payments").status_code == 200
assert ck_.get("/admin/settings").status_code == 403
assert c.get("/admin/security").status_code == 200                   # этот ПК — всегда
post(c, "/admin/security", {"action": "ip_default"})
with A.app.app_context():
    assert A.get_setting("ip_allow") == sec.DEFAULT_IP_ALLOW and A.get_setting("ip_admin_allow") == ""
assert ck_.get("/payments").status_code == 403

# ---- подбор с одного адреса по разным логинам (20 за 15 мин.) -> блок адреса
post(c, "/admin/security", {"action": "ip", "ip_allow": "192.168.1.0/24", "ip_admin_allow": ""})
cx = client(LAN2)
for i in range(20):
    login(cx, f"user{i}", "x")
r = login(cx, "kassir", "Tolov2026")
assert r.status_code == 429, r.status_code
post(c, "/admin/security", {"action": "unlock", "username": "kassir", "ip": LAN2})
assert login(cx, "kassir", "Tolov2026").status_code == 302

# ---- автовыход: ҳамма — 30 мин. бездействия (эгаси, 24.10)
ci = client(); assert login(ci).status_code == 302
assert ci.get("/").status_code == 200
with ci.session_transaction() as s:
    s["seen"] = time.time() - 31 * 60
r = ci.get("/tenants")
assert r.status_code == 302 and "/login" in r.headers["Location"]
assert ci.get("/tenants").status_code == 302
assert "бездействия" in ci.get("/login").get_data(as_text=True)
ck2 = client(); assert login(ck2, "kassir", "Tolov2026").status_code == 302
with ck2.session_transaction() as s:
    s["seen"] = time.time() - 29 * 60
assert ck2.get("/payments").status_code == 200
with ck2.session_transaction() as s:
    s["seen"] = time.time() - 31 * 60
assert ck2.get("/payments").status_code == 302

# ---- устаревший хэш обновляется при входе; смена стандартного пароля убирает предупреждение
k.execute("UPDATE users SET password_hash=? WHERE username='kassir'", (generate_password_hash("Tolov2026", method="pbkdf2:sha256:1000"),)); k.commit()
assert login(client(), "kassir", "Tolov2026").status_code == 302
assert k.execute("SELECT password_hash FROM users WHERE username='kassir'").fetchone()[0].startswith("scrypt:")
c = client(); login(c)
aid = k.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
post(c, "/admin/users", {"id": str(aid), "full_name": "Админ", "role": "admin", "active": "on", "password": "Hisob2026x"})
assert k.execute("SELECT weak_pw FROM users WHERE id=?", (aid,)).fetchone()[0] == 0
assert "Стандартный пароль у" not in c.get("/").get_data(as_text=True)
assert login(client(), "admin", "Hisob2026x").status_code == 302
# ---- страница «Безопасность»: статус резервных копий, вне ПК — в облачную папку, хранятся последние 30
import backup as B, glob, json as _j
off = os.path.join(tmp, "cloud"); os.makedirs(off)
os.environ["RENT_OFFSITE_DIR"] = off
B.DB, B.DATA, B.OUT = A.DB_PATH, tmp, os.path.join(tmp, "backups")
for i in range(33):
    open(os.path.join(B.OUT if os.path.isdir(B.OUT) else (os.makedirs(B.OUT) or B.OUT), f"rent_2020-01-01_{i:04d}.db"), "w").close()
B.main()
assert len(glob.glob(os.path.join(B.OUT, "rent_*.db"))) == 30
assert len(glob.glob(os.path.join(off, "LumbertureHub-backups", "rent_*.db"))) == 1
st = _j.load(open(os.path.join(tmp, "backup_status.json"), encoding="utf-8"))
assert st["integrity"] == "ok" and st["offsite_result"].startswith("OK") and not glob.glob(os.path.join(off, "**", "*.session"), recursive=True)
h = c.get("/admin/security").get_data(as_text=True)
assert "LumbertureHub-backups" in h and "<b>30</b> копий" in h
del os.environ["RENT_OFFSITE_DIR"]
# ---- оформление: тема/акцент/шрифт/плотность/название, логотип только png/jpg ≤ 1 МБ, сброс, предпросмотр
import struct, zlib
def png(w=2, h=2):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
def upload_logo(c, raw, name):
    return c.post("/admin/appearance", data={"csrf": tok(c), "ui_theme": "dark", "ui_accent": "blue", "ui_accent_custom": "#112233",
                                             "ui_font": "large", "ui_density": "compact", "ui_title": "Цех Дилшод",
                                             "logo": (io.BytesIO(raw), name)}, content_type="multipart/form-data", follow_redirects=True)
assert A.logo_type(png()) == "png" and A.logo_type(b"\xff\xd8\xff\xe0" + b"x" * 10) == "jpg" and A.logo_type(b"<svg></svg>") is None
r = upload_logo(c, b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "logo.svg")
assert "только PNG или JPG" in r.get_data(as_text=True)
r = upload_logo(c, b"\xff\xd8\xff" + b"x" * (A.UI_LOGO_MAX + 5), "big.jpg")
assert "не больше 1 МБ" in r.get_data(as_text=True)
assert not os.path.exists(os.path.join(tmp, "ui_logo.png"))
r = upload_logo(c, png(), "evil.svg")          # расширение врёт — тип определяется по содержимому
assert "Оформление сохранено" in r.get_data(as_text=True) and os.path.exists(os.path.join(tmp, "ui_logo.png"))
with A.app.app_context():
    assert A.get_setting("ui_theme") == "dark" and A.get_setting("ui_accent") == "blue" and A.get_setting("ui_font") == "large" \
        and A.get_setting("ui_density") == "compact" and A.get_setting("ui_title") == "Цех Дилшод" and A.get_setting("ui_logo") == "png"
h = c.get("/").get_data(as_text=True)
assert 'data-theme="dark"' in h and 'data-font="large"' in h and 'data-density="compact"' in h and "Цех Дилшод" in h and 'src="/ui/logo' in h
lg = c.get("/ui/logo")
assert lg.status_code == 200 and lg.mimetype == "image/png" and lg.data.startswith(b"\x89PNG")
css = c.get("/ui/theme.css").get_data(as_text=True)
assert css.strip() == ":root{--g:#1f5fae}", repr(css)
lo = client(); h = lo.get("/login").get_data(as_text=True)          # оформление общее, видно и до входа
assert 'data-theme="dark"' in h and "Цех Дилшод" in h and 'class="loginlogo"' in h
# свой цвет
post(c, "/admin/appearance", {"ui_theme": "auto", "ui_accent": "custom", "ui_accent_custom": "#ab12cd", "ui_font": "xlarge", "ui_density": "normal"})
assert c.get("/ui/theme.css").get_data(as_text=True).strip() == ":root{--g:#ab12cd}"
post(c, "/admin/appearance", {"ui_theme": "light", "ui_accent": "custom", "ui_accent_custom": "red"})
assert "формате #RRGGBB" in c.get("/admin/appearance").get_data(as_text=True)
# сброс
post(c, "/admin/appearance", {"action": "reset"})
with A.app.app_context():
    assert A.get_setting("ui_theme") == "light" and A.get_setting("ui_accent") == "green" and A.get_setting("ui_logo") == "" and A.get_setting("ui_title") == ""
assert not os.path.exists(os.path.join(tmp, "ui_logo.png")) and c.get("/ui/logo").status_code == 404
h = c.get("/admin/appearance").get_data(as_text=True)
assert "Сбросить" in h and 'value="brand"' in h and 'value="dark"' in h and 'value="compact"' in h and 'value="custom"' in h and "appearance.js" in h
assert client().get("/admin/appearance").status_code == 302
# стиль «Современный (дашборд)»: html data-skin, Главная — KPI карточкалари; «Классический» — йўқ
assert 'value="v16"' in h and 'data-skin="classic"' in c.get("/").get_data(as_text=True) and 'class="kpis"' not in c.get("/").get_data(as_text=True)
post(c, "/admin/appearance", {"ui_theme": "v16", "ui_accent": "lumberture"})
h = c.get("/").get_data(as_text=True)
assert 'data-skin="v16"' in h and 'data-theme="light"' in h and 'class="kpis"' in h and "Долг клиентов" in h, h[:300]
assert 'value="v16" data-ui="theme" checked' in c.get("/admin/appearance").get_data(as_text=True)
with A.app.app_context():                                  # олдинги созлама (ui_skin=v16, тема светлая) — «Современный» деб кўринади
    A.set_settings(ui_theme="light", ui_skin="v16")
assert 'data-skin="v16"' in c.get("/").get_data(as_text=True)
post(c, "/admin/appearance", {"ui_theme": "light", "ui_accent": "lumberture"})
assert 'data-skin="classic"' in c.get("/").get_data(as_text=True)
post(c, "/admin/appearance", {"action": "reset"})
# ўз оформлениеси (27.10): ҳар фойдаланувчи ўзига — бошқаларга таъсир қилмайди; админ саҳифаси — умумий қийматлар
with A.app.app_context():
    from werkzeug.security import generate_password_hash as _gph
    A.db().execute("INSERT OR IGNORE INTO users(username,password_hash,role,active) VALUES('uiuser',?,'basis',1)", (_gph("Uiuser12345!"),)); A.db().commit()
cu = client(); login(cu, "uiuser", "Uiuser12345!")
h = cu.get("/my/appearance").get_data(as_text=True)
assert cu.get("/my/appearance").status_code == 200 and "Сохранить для себя" in h and 'name="ui_title"' not in h and 'name="logo"' not in h
post(cu, "/my/appearance", {"ui_theme": "dark", "ui_accent": "custom", "ui_accent_custom": "#aa3311", "ui_font": "large", "ui_density": "compact"})
h = cu.get("/my/appearance").get_data(as_text=True)
assert 'data-theme="dark"' in h and 'data-font="large"' in h and "Как у всех" in h
assert cu.get("/ui/theme.css").get_data(as_text=True).strip() == ":root{--g:#aa3311}" and "private" in cu.get("/ui/theme.css").headers["Cache-Control"]
hh = c.get("/").get_data(as_text=True)
assert 'data-theme="light"' in hh and 'data-font="normal"' in hh                               # админга таъсир қилмади
post(c, "/my/appearance", {"ui_theme": "dark", "ui_accent": "blue"})                              # админнинг ўзиники
assert 'data-theme="dark"' in c.get("/").get_data(as_text=True)
ha = c.get("/admin/appearance").get_data(as_text=True)
assert 'data-theme="light"' in ha and 'value="light" data-ui="theme" checked' in ha and "Моё оформление" in ha   # «для всех» — умумий қийматлар
post(cu, "/my/appearance", {"action": "reset"})
assert 'data-theme="light"' in cu.get("/my/appearance").get_data(as_text=True) and "Как у всех" not in cu.get("/my/appearance").get_data(as_text=True)
post(c, "/my/appearance", {"action": "reset"})
# тёмная палитра задаёт все цвета светлой (кроме производных от акцента), лишних «жёстких» цветов в CSS нет
css = open(os.path.join(os.path.dirname(A.__file__), "static", "style.css"), encoding="utf-8").read()
light = set(re.findall(r"(--[\w-]+):", css[css.index(":root{"):css.index("}", css.index(":root{"))]))
dark_blk = css[css.index(":root[data-theme=dark]{"):]
dark = set(re.findall(r"(--[\w-]+):", dark_blk[:dark_blk.index("}")]))
assert light - dark <= {"--g", "--on-g", "--on-g-mut", "--nav-txt"}, light - dark
assert "@media (prefers-color-scheme:dark){:root[data-theme=auto]{" in css
body = css.split("color-scheme:dark}}")[-1].split("/* ===================== Замонавий")[0]   # замонавий қатлам — ўз палитраси
assert set(re.findall(r"#[0-9a-fA-F]{3,6}\b", body)) <= {"#fff", "#000", "#1f2a27", "#d7e6e0"}, set(re.findall(r"#[0-9a-fA-F]{3,6}\b", body))
# подписи видов сообщений в журнале — текст, а не кортеж шаблона (регрессия после шаблонов по языкам)
assert all(isinstance(v, str) for v in A.MSG_KINDS.values()) and all(isinstance(v, tuple) for v in A.TPL_KINDS.values())
assert "(&#39;" not in c.get("/messages").get_data(as_text=True)
k.close()
print("SECURITY TESTS PASSED")
