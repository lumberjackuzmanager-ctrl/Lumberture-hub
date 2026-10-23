"""Роллар ва доступ («Сервис → Пользователи»).

Роль — бўлим/лавозим: Администратор, Бухгалтер, Кассир, Менеджер (крой), Конструктор (Базис) ва админ қўшган янгилари
(масалан, янги бўлим очилса — «Склад», «Доставка»). Ҳар бир ролнинг тури:
  admin   — ҳаммаси (фақат «Администратор» роли);
  full    — ҳаммаси, созламалар (админ саҳифалари) дан ташқари;
  limited — фақат белгиланган бўлимлар (SECTIONS).
Фойдаланувчига битта роль берилади; товар/мижоз қўшиш, ўчириш, лимит каби рухсатлар — фойдаланувчининг ўзида."""
import json, re

A = None

COMMON = {"static", "favicon", "ui_theme_css", "ui_logo", "login", "logout", "set_lang", "setup_shortcut",
          "print_pdf_file", "print_prepare", "print_preview", "print_printers", "print_send", "erp_api_rate", "api_clientlog"}

_DOC = {"erp_docs", "erp_doc", "erp_doc2_print", "erp_doc_print", "erp_doc_status", "erp_doc_lock", "erp_doc_resp"}
_PICK = {"erp_api_goods", "erp_api_gtree", "erp_api_glist", "erp_api_decor", "erp_goods"}
_CLI = {"erp_api_clients", "erp_api_credit", "erp_client_form"}

# (калит, номи, гуруҳ, endpoint'лар, бош саҳифа (endpoint, args), изоҳ)
SECTIONS = [
    ("kassa", "Кассовый день", "Касса", {"kassa_day", "kassa_day_save", "kassa_lock", "kassa_meals", "kassa_month", "kassa_month_pdf", "kassa_wcash"},
     ("kassa_day", {}), "приход-расход за день, обеды, итог месяца (просмотр)"),
    ("kassa_month", "Итоги месяца и KPI", "Касса", {"kassa_month", "kassa_month_save", "kassa_month_pdf", "kassa_kpi"}, ("kassa_month", {}), "прибыль, распределение, KPI"),
    ("staff_pay", "Табель и зарплата", "Касса", {"kassa_staff"}, ("kassa_staff", {}), "видны зарплаты всех сотрудников"),
    ("sales", "Накладные (продажа)", "Продажи и склад", _DOC | _PICK | _CLI | {"erp_sale_edit", "erp_basis", "erp_basis_list", "setup_basis_js", "erp_basis_last", "erp_basis_print"},
     ("erp_docs", {"kind": "sale"}), "выписывать и печатать накладные, проекты из Базиса"),
    ("pay_in", "Поступление денег", "Продажи и склад", _DOC | _CLI | {"erp_doc2_edit"}, ("erp_docs", {"kind": "pay"}), "оплаты от клиентов"),
    ("receipts", "Поступление товара (приход)", "Продажи и склад", _DOC | _PICK | _CLI | {"erp_doc2_edit", "rep_receipts", "erp_suppliers", "erp_api_inv_fill", "erp_api_cat_tree"},
     ("erp_docs", {"kind": "receipt"}), "приход от поставщиков и инвентаризация"),
    ("goods", "Товары и каталог", "Продажи и склад", _PICK | {"erp_good_form", "erp_catalog", "erp_decors", "erp_eman", "erp_api_cat_tree", "erp_api_cat_list",
                                                              "erp_api_cat_item", "erp_api_cat_save", "erp_api_cat_c1src", "erp_api_gfolder", "erp_api_gmove"},
     ("erp_goods", {}), "справочник товаров, декоры, каталог"),
    ("stock", "Остаток товаров", "Продажи и склад", {"rep_stock", "c1_stock"}, ("rep_stock", {}), ""),
    ("logist", "Логистика (водители)", "Продажи и склад", {"erp_logist"}, ("erp_logist", {}), "рейсы и расходы водителей, расчёт за неделю"),
    ("online", "Онлайн-заказы (сайт)", "Продажи и склад", {"erp_online", "erp_online_view", "erp_online_json"}, ("erp_online", {}),
     "заказы с сайта: проверка, черновик накладной, статусы клиенту"),
    ("pickup", "Олиб келиш (шофёрлар)", "Продажи и склад", {"erp_pickup"}, ("erp_pickup", {}), "1С'да минусга кетган товарлар, шофёрга юбориш"),
    ("clients", "Клиенты", "Клиенты и отчёты", {"erp_clients", "erp_history", "erp_client_card"}, ("erp_clients", {}), "список клиентов и их история"),
    ("mutual", "Взаиморасчеты", "Клиенты и отчёты", {"rep_mutual"}, ("rep_mutual", {}), "долги и оплаты по клиентам"),
    ("rep_sales", "Отчет по накладным", "Клиенты и отчёты", {"rep_sales"}, ("rep_sales", {}), "продажи за период"),
    ("debts1c", "1С: долги, продажи, оплаты", "Клиенты и отчёты", {"c1_clients", "c1_client", "c1_sales", "c1_sale", "c1_sale_print", "c1_payments", "c1_receipts"},
     ("c1_clients", {}), "только просмотр базы 1С"),
    ("workshop", "Цех: работы и заработок", "Цех", {"kassa_raspil", "kassa_raspil_report", "kassa_raspil_photo"}, ("kassa_raspil", {}), "кто что сделал; видны суммы заработка всех сотрудников"),
    ("ready", "Заказ готов («тайёр»)", "Цех", {"ready_page", "ready_photo"}, ("ready_page", {}), "сообщения о готовых заказах"),
    ("staff", "Сотрудники (список)", "Цех", {"staff_list", "staff_card"}, ("staff_list", {}), "карточки без изменения"),
    ("docs", "Документы на бланке", "Документы", {"docs_list", "docs_card", "docs_form", "docs_file", "docs_rebuild"}, ("docs_list", {}), "договоры, КП, письма, акты"),
    ("rent", "Аренда", "Аренда", {"tenants", "tenant_card", "tenant_form", "statement", "statement_xlsx", "tenant_charge", "tenant_invoice", "tenant_opening",
                                  "tenant_payment", "tenant_qr", "tenant_send_balance", "tenant_tg", "tenant_tg_card", "payments", "readings", "billing",
                                  "reports", "reports_xlsx", "clients", "client_card", "client_form", "client_debt", "client_payment", "client_opening",
                                  "client_remind", "client_report_pdf", "client_report_send", "client_sms_settings", "client_tg", "clients_xlsx"},
     ("tenants", {}), "арендаторы, оплаты, счётчики, счета"),
    ("epay", "Click / Payme", "Прочее", {"erp_epay"}, ("erp_epay", {}), "поступления Click и Payme"),
    ("messages", "Журнал сообщений", "Прочее", {"messages"}, ("messages", {}), "что ушло клиентам в Telegram/SMS"),
    ("rates", "Курс $", "Прочее", {"rates_page"}, ("rates_page", {}), ""),
]
SEC = {s[0]: s for s in SECTIONS}
GROUPS = []
for _s in SECTIONS:
    if _s[2] not in GROUPS:
        GROUPS.append(_s[2])
OLD_KASSA_OPT = {"kassa_v_clients": "clients", "kassa_v_receipts": "receipts", "kassa_v_mutual": "mutual", "kassa_v_stock": "stock"}
KINDS = {"full": "Всё, кроме настроек", "limited": "Только выбранные разделы"}

BUILTIN = [
    ("admin", "Администратор", "admin", [], "Всё, включая настройки, пользователей, удаление и закрытие периода.", 10),
    ("cashier", "Бухгалтер", "limited", ["kassa", "kassa_month", "staff_pay", "mutual", "rep_sales", "debts1c", "epay", "stock", "clients"],
     "Касса, итоги месяца, зарплата, взаиморасчёты и отчёты. Другое — если откроет администратор.", 20),
    ("kassa", "Кассир", "limited", ["kassa", "sales", "pay_in"], "Кассовый день, накладные и поступление денег.", 30),
    ("manager", "Менеджер (крой)", "limited", ["sales", "clients", "goods", "stock", "docs"], "Заказы и крой: накладные, клиенты, товары и остатки, документы.", 40),
    ("basis", "Конструктор (Базис)", "limited", ["sales", "clients", "goods", "stock", "docs"], "Накладные (черновик), клиенты, товары и остатки, документы. Работа и заработок — в цех-боте.", 50),
]

SCHEMA = """CREATE TABLE IF NOT EXISTS roles(key TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'limited',
  sections TEXT NOT NULL DEFAULT '[]', note TEXT DEFAULT '', builtin INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 100,
  d_add INTEGER NOT NULL DEFAULT 1, d_super INTEGER NOT NULL DEFAULT 0, d_limit INTEGER NOT NULL DEFAULT 0, d_nal INTEGER NOT NULL DEFAULT 0,
  can_post INTEGER NOT NULL DEFAULT 0)"""

_info = {}


def reload(con=None):
    """roles жадвалини ўқиб, A.ROLES (номлар) ва ички кешни янгилайди."""
    own = con is None
    con = con or A.connect()
    try:
        rows = con.execute("SELECT * FROM roles ORDER BY sort, name").fetchall()
    finally:
        if own:
            con.close()
    info = {}
    for r in rows:
        d = dict(r)
        try:
            d["secs"] = [s for s in json.loads(d["sections"] or "[]") if s in SEC]
        except ValueError:
            d["secs"] = []
        info[d["key"]] = d
    _info.clear()
    _info.update(info)
    A.ROLES.clear()
    A.ROLES.update({k: v["name"] for k, v in info.items()})


ACC_SECS = ["kassa", "kassa_month", "staff_pay", "mutual", "rep_sales", "debts1c", "epay", "stock", "clients"]


def seed(con):
    con.execute(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(roles)")}
    if "can_post" not in cols:                     # «Провести» — фақат кассир (касса пулни олади ёки насияга беради)
        con.execute("ALTER TABLE roles ADD COLUMN can_post INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE roles SET can_post=1 WHERE key='kassa'")
        # Бухгалтер — фақат бухгалтерия ишлари (аввал «всё, кроме настроек» эди; админ ўзгартирмаган бўлса)
        con.execute("UPDATE roles SET kind='limited', sections=?, note=? WHERE key='cashier' AND kind='full' AND sections='[]'",
                    (json.dumps(ACC_SECS), "Касса, итоги месяца, зарплата, взаиморасчёты и отчёты. Другое — если откроет администратор."))
    have = {r[0] for r in con.execute("SELECT key FROM roles")}
    for key, name, kind, secs, note, sort in BUILTIN:
        if key in have:
            continue
        if key == "kassa":                         # аввалги «Касса» созламаларидан (админ ёққан қўшимча бўлимлар)
            for k, s in OLD_KASSA_OPT.items():
                r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
                if r and r[0] == "1":
                    secs = secs + [s]
        con.execute("INSERT INTO roles(key, name, kind, sections, note, builtin, sort, d_add, d_super, can_post) VALUES(?,?,?,?,?,1,?,?,?,?)",
                    (key, name, kind, json.dumps(secs), note, sort, 0 if key == "basis" else 1, 1 if key == "admin" else 0, 1 if key == "kassa" else 0))
    con.commit()


def role(key):
    return _info.get(key) or _info.get("kassa") or {"key": key, "name": key, "kind": "limited", "secs": [], "note": ""}


def kind_of(u):
    if not u:
        return "limited"
    if u["role"] == "admin":
        return "admin"
    return role(u["role"])["kind"]


def is_limited(u=None):
    u = u if u is not None else A.current_user()
    return bool(u) and kind_of(u) == "limited"


def sections_of(u=None):
    u = u if u is not None else A.current_user()
    if not u:
        return set()
    if kind_of(u) != "limited":
        return set(SEC)
    return set(role(u["role"])["secs"])


def has_sec(key, u=None):
    return key in sections_of(u)


def ep_allowed(ep, u=None):
    u = u if u is not None else A.current_user()
    if not u or kind_of(u) != "limited":
        return True
    if ep in COMMON:
        return True
    return any(ep in SEC[s][3] for s in sections_of(u))


def home(u=None):
    """Чекланган фойдаланувчининг бош саҳифаси — биринчи рухсат этилган бўлим."""
    from flask import url_for
    ss = sections_of(u)
    for s in SECTIONS:
        if s[0] in ss:
            return url_for(s[4][0], **s[4][1])
    return url_for("logout")


def can_post(u=None):
    """Ҳужжатни проведен қилиш / проведенияни бекор қилиш: админ ва «Может проводить» белгили роль (Кассир)."""
    u = u if u is not None else A.current_user()
    if not u:
        return False
    if u["role"] == "admin":
        return True
    return bool(role(u["role"]).get("can_post"))


def doc_kinds(u=None):
    """Чекланган фойдаланувчи кўрадиган/ёзадиган ҳужжат турлари."""
    ss = sections_of(u)
    return tuple(k for k, s in (("sale", "sales"), ("pay", "pay_in"), ("receipt", "receipts"), ("reval", "goods"), ("inv", "receipts")) if s in ss)


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        seed(con)
        reload(con)
    finally:
        con.close()
    from flask import request, render_template, redirect, url_for, flash, abort, session
    import sqlite3

    def users_ctx(edit=None, new_role=None):
        rows = A.q("SELECT u.*, (SELECT w.name FROM k_workers w WHERE w.user_id=u.id LIMIT 1) worker FROM users u ORDER BY u.active DESC, u.id")
        cnt = {}
        for r in rows:
            if r["active"]:
                cnt[r["role"]] = cnt.get(r["role"], 0) + 1
        workers = A.q("SELECT id, name, fio, user_id FROM k_workers WHERE COALESCE(svc,0)=0 AND (fired IS NULL OR fired='') AND (active=1 OR user_id IS NOT NULL) ORDER BY name COLLATE NOCASE")
        return dict(rows=rows, edit=edit, RINFO=list(_info.values()), cnt=cnt, SECTIONS=SECTIONS, GROUPS=GROUPS, SEC=SEC, KINDS=KINDS,
                    workers=workers, new_role=new_role)

    def save_user(f):
        db = A.db()
        rk = f.get("role") if f.get("role") in _info else "kassa"
        perms = [1 if f.get(k) else 0 for k in ("p_add", "p_super", "p_limit", "p_nal")]
        uid = int(A.parse_float(f.get("id"), 0))
        me = A.current_user()["id"]
        un = (f.get("username") or "").strip()
        if un and not re.fullmatch(r"[\w.\-@]{2,40}", un):
            raise ValueError("Логин — буквы, цифры, точка или дефис, без пробелов, 2–40 символов")
        if uid:
            if uid == me:
                rk = "admin"
            if un:
                if A.q("SELECT 1 FROM users WHERE username=? COLLATE NOCASE AND id<>?", (un, uid), one=True):
                    raise ValueError("Такой логин уже есть")
                db.execute("UPDATE users SET username=? WHERE id=?", (un, uid))
            db.execute("UPDATE users SET full_name=?, role=?, active=?, p_add=?, p_super=?, p_limit=?, p_nal=? WHERE id=?",
                       (f.get("full_name", "").strip(), rk, 1 if f.get("active") or uid == me else 0, *perms, uid))
            if f.get("password"):
                urow = A.q("SELECT username FROM users WHERE id=?", (uid,), one=True)
                bad = A.sec.password_problem(f["password"], urow["username"] if urow else "")
                if bad:
                    raise ValueError(bad[0].upper() + bad[1:])
                db.execute("UPDATE users SET password_hash=?, weak_pw=0 WHERE id=?", (A.generate_password_hash(f["password"]), uid))
        else:
            if not un:
                raise ValueError("Логин обязателен")
            if A.q("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (un,), one=True):
                raise ValueError("Такой логин уже есть")
            bad = A.sec.password_problem(f.get("password", ""), un)
            if bad:
                raise ValueError(bad[0].upper() + bad[1:])
            uid = db.execute("INSERT INTO users(username, password_hash, full_name, role, weak_pw, p_add, p_super, p_limit, p_nal) VALUES(?,?,?,?,0,?,?,?,?)",
                             (un, A.generate_password_hash(f["password"]), f.get("full_name", "").strip(), rk, *perms)).lastrowid
        wid = int(A.parse_float(f.get("worker"), 0))
        if "worker" in f:
            db.execute("UPDATE k_workers SET user_id=NULL WHERE user_id=?", (uid,))
            if wid:
                db.execute("UPDATE k_workers SET user_id=? WHERE id=?", (uid, wid))
        db.commit()

    @app.route("/admin/users", methods=["GET", "POST"])
    @A.admin_required
    def users():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    if int(f["id"]) == A.current_user()["id"]:
                        raise ValueError("Нельзя отключить себя")
                    A.db().execute("UPDATE users SET active=0 WHERE id=?", (f["id"],))
                    A.db().commit()
                    flash("Пользователь отключён", "ok")
                elif f.get("action") == "enable":
                    A.db().execute("UPDATE users SET active=1 WHERE id=?", (f["id"],))
                    A.db().commit()
                    flash("Пользователь включён", "ok")
                else:
                    save_user(f)
                    flash("Сохранено", "ok")
            except (ValueError, sqlite3.IntegrityError) as e:
                flash(("Такой логин уже есть" if isinstance(e, sqlite3.IntegrityError) else str(e)), "error")
                if not f.get("id"):                    # ёзилганлар йўқолмасин (паролдан ташқари)
                    session["uform"] = {k: f.get(k, "") for k in ("username", "full_name", "role")}
                else:
                    return redirect(url_for("users", edit=f.get("id")))
            return redirect(url_for("users"))
        edit = A.q("SELECT u.*, (SELECT w.id FROM k_workers w WHERE w.user_id=u.id LIMIT 1) wid FROM users u WHERE u.id=?",
                   (request.args.get("edit"),), one=True) if request.args.get("edit") else None
        return render_template("users.html", **users_ctx(edit), form={} if edit else (session.pop("uform", None) or {}))

    @app.route("/admin/roles/<key>", methods=["GET", "POST"])
    @A.admin_required
    def role_form(key):
        r = _info.get(key) if key != "new" else None
        if key != "new" and not r:
            abort(404)
        if r and r["kind"] == "admin":
            flash("Администратор всегда видит всё — эту роль менять не нужно", "warn")
            return redirect(url_for("users") + "#roles")
        if request.method == "POST":
            f = request.form
            db = A.db()
            if f.get("action") == "delete" and r and not r["builtin"]:
                if A.q("SELECT 1 FROM users WHERE role=? AND active=1", (key,), one=True):
                    flash("У этой роли есть пользователи — сначала дайте им другую роль", "error")
                    return redirect(url_for("role_form", key=key))
                db.execute("DELETE FROM roles WHERE key=?", (key,))
                db.execute("UPDATE users SET role='kassa' WHERE role=?", (key,))
                db.commit()
                reload()
                flash("Роль удалена", "ok")
                return redirect(url_for("users") + "#roles")
            name = re.sub(r"\s+", " ", f.get("name") or "").strip()[:60]
            kind = f.get("kind") if f.get("kind") in KINDS else "limited"
            secs = [s[0] for s in SECTIONS if f.get("s_" + s[0])]
            if not name:
                flash("Укажите название роли", "error")
                return redirect(request.url)
            if kind == "limited" and not secs:
                flash("Отметьте хотя бы один раздел", "error")
                return redirect(request.url)
            dflt = [1 if f.get(k) else 0 for k in ("d_add", "d_super", "d_limit", "d_nal")]
            cpost = 1 if f.get("can_post") else 0
            note = (f.get("note") or "").strip()[:200]
            if r:
                db.execute("UPDATE roles SET name=?, kind=?, sections=?, note=?, d_add=?, d_super=?, d_limit=?, d_nal=?, can_post=? WHERE key=?",
                           (name, kind, json.dumps(secs), note, *dflt, cpost, key))
            else:
                base = re.sub(r"[^a-z0-9]+", "", name.lower()) or "role"
                k, i = "r_" + base[:20], 1
                while k in _info or k in ("admin", "new"):
                    i += 1
                    k = f"r_{base[:18]}{i}"
                mx = A.q("SELECT COALESCE(MAX(sort),100) m FROM roles", one=True)["m"]
                db.execute("INSERT INTO roles(key, name, kind, sections, note, builtin, sort, d_add, d_super, d_limit, d_nal, can_post) "
                           "VALUES(?,?,?,?,?,0,?,?,?,?,?,?)", (k, name, kind, json.dumps(secs), note, mx + 10, *dflt, cpost))
            db.commit()
            reload()
            flash(f"Роль «{name}» сохранена", "ok")
            return redirect(url_for("users") + "#roles")
        users_n = A.q("SELECT COUNT(*) n FROM users WHERE role=? AND active=1", (key,), one=True)["n"] if r else 0
        return render_template("role_form.html", r=r, SECTIONS=SECTIONS, GROUPS=GROUPS, KINDS=KINDS, users_n=users_n)
