"""Справочники → Сотрудники: битта ишчи — битта карточка.

Аввал ишчи маълумоти тўрт жойда эди: «Табель и зарплата» (қабул/бўшатиш, ставка), «Статьи кассы» (исм, Ф.И.Ш., валюта),
«Цех ишлари» (бот рақамлари, станок доступи, крой менежер) ва «Буюртма тайёр» (Telegram рақамлари), логин эса «Пользователи»да.
Энди ҳаммаси ишчи карточкасида. Маълумот ўша жадвалларда қолади (k_workers, ready_workers, users) — бот, табель, KPI ва
«тайёр» хабарлари аввалгидек ишлайди; бу модуль фақат уларни бир жойдан ўзгартиради."""
import json, re
from datetime import date

A = None

POSITIONS = ("Распил (арра)", "Двухпилка", "Кромка", "Овал кромка", "Присадка", "Ровер (ЧПУ)", "Утолщенка", "Кройщик", "Конструктор (Базис)",
             "Начальник цеха", "Менеджер", "Кассир", "Бухгалтер", "Кладовщик", "Водитель", "Грузчик", "Помощник", "Уборщица")
ROLES = {"raspil": "Станочник — бот: станки, распил, приход-уход",
         "staff": "Бот: только приход-уход",
         "ready": "Только «готово» (офисный Telegram)"}
# Номер учун тайёр варианти: (номи, нима қилади, role, boss, stajer, kroy, mgr)
PRESETS = {
    "operator": ("Станочник", "Работает на станках (распил, кромка, присадка, ровер…). В боте: «Пришёл / Ушёл», фото накладной, отметка работы. Зарплата сдельно по KPI.", "raspil", 0, 0, 0, 0),
    "boss": ("Начальник цеха", "Всё как у станочника, но на всех станках. Видит работу всего цеха.", "raspil", 1, 0, 0, 0),
    "stajer": ("Стажёр", "Учится, помогает на станке. В боте только «Пришёл / Ушёл», работа не считается сдельно.", "raspil", 0, 1, 0, 0),
    "kroy": ("Кройщик (менеджер по крою)", "В боте «Пришёл / Ушёл» и кнопка «Крой қилдим» — сделанные кройки идут в его зарплату.", "staff", 0, 0, 1, 0),
    "mgr": ("Менеджер офиса", "В боте получает работы цеха без накладной (Ц-коды) и уведомления по кускам, вносит их в 1С.", "staff", 0, 0, 0, 1),
    "basis": ("Конструктор (Базис)", "Проектирует заказы в Базисе. Зарплата — % от «Услуги конструктора» в накладных 1С, считается сама, без бота. В боте только «Пришёл / Ушёл».", "staff", 0, 0, 0, 0),
    "driver": ("Шофёр", "Получает в цех-боте список «Олиб келиш» (что забрать у поставщиков) и отмечает «Олдим» / «Йўқ экан». Бот также отмечает приход и уход.", "staff", 0, 0, 0, 0),
    "staff": ("Другой сотрудник", "Водитель, грузчик, кладовщик, уборщица и т.п. Бот только отмечает приход и уход для табеля.", "staff", 0, 0, 0, 0),
    "ready": ("Только «тайёр», без бота", "Пишет «тайёр» с фото накладной в офисный Telegram — клиенту уходит «заказ готов». Бот не нужен.", "ready", 0, 0, 0, 0),
}


def preset_of(p, basis=False):
    if p["role"] == "ready":
        return "ready"
    if p["role"] == "raspil":
        return "boss" if p.get("boss") else "stajer" if p.get("stajer") else "operator"
    return "kroy" if p.get("kroy") else "mgr" if p.get("mgr") else "driver" if p.get("driver") else "basis" if basis else "staff"


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(k_workers)")}
    for col, ddl in (("position", "TEXT DEFAULT ''"), ("phone", "TEXT DEFAULT ''"), ("hired", "TEXT"), ("note", "TEXT DEFAULT ''"),
                     ("user_id", "INTEGER")):
        if col not in cols:
            con.execute(f"ALTER TABLE k_workers ADD COLUMN {col} {ddl}")


def _clean(s, n):
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def _date(s):
    try:
        return date.fromisoformat((s or "").strip()).isoformat()
    except ValueError:
        return None


def status(w):
    if w["svc"]:
        return "svc"
    if w["fired"]:
        return "fired"
    return "on" if w["active"] else "off"


def first_month(wid):
    r = A.q("SELECT MIN(ym) m FROM k_ws WHERE worker_id=?", (wid,), one=True)
    return r["m"] if r and r["m"] else ""


def basis_of(wid):
    """-> (шу ишчига боғланган 1С конструктор папкалари [(id, номи)], ҳамма папкалар) — 1С ўқилмаса бўш."""
    import raspil as P
    try:
        des = P.basis_designers()
        mp = P.basis_map()
    except Exception as e:
        A.errlog.error("staff basis: %r", e)
        return [], []
    return [(fid, nm) for fid, nm in des if mp.get(fid) == wid], des


def phones_of(wid, basis=False):
    import raspil as P
    out = []
    for r in A.q("SELECT * FROM ready_workers WHERE kw_id=? ORDER BY active DESC, id", (wid,)):
        r = dict(r)
        pp = None if r.get("perms") in (None, "") else set(json.loads(r["perms"] or "[]"))
        r["pset"] = pp
        r["preset"] = preset_of(r, basis)
        r["ptext"] = ("начальник цеха — все станки" if r.get("boss") else "стажёр" if r.get("stajer") else
                      "не указаны — открыты все" if pp is None else perms_ru(pp, P))
        out.append(r)
    return out


def perms_ru(pp, P):
    if not pp:
        return "нет"
    out = []
    for m in P.MACHINES:
        op, hp = f"{m}:op" in pp, f"{m}:hp" in pp
        if op or hp:
            out.append(P.mlabel(m) + (" (главный и помощник)" if op and hp else " (главный)" if op else " (помощник)"))
    return ", ".join(out)


def fire(db, wid, fd):
    """Ишдан бўшатиш: шу ой итогида қолади; кейинги ойларда пул берилмаган бўлса — рўйхатдан чиқади; бот ва логин ўчади."""
    db.execute("UPDATE k_workers SET fired=?, active=0 WHERE id=?", (fd, wid))
    for (nym,) in db.execute("SELECT ym FROM k_ws WHERE worker_id=? AND ym>?", (wid, fd[:7])).fetchall():
        if not db.execute("SELECT 1 FROM k_cells WHERE ym=? AND kind='wage' AND ref_id=? AND (uzs<>0 OR usd<>0)", (nym, wid)).fetchone():
            db.execute("DELETE FROM k_ws WHERE ym=? AND worker_id=?", (nym, wid))
    db.execute("UPDATE ready_workers SET active=0 WHERE kw_id=?", (wid,))
    u = db.execute("SELECT user_id FROM k_workers WHERE id=?", (wid,)).fetchone()
    if u and u[0] and u[0] != A.current_user()["id"]:
        db.execute("UPDATE users SET active=0 WHERE id=?", (u[0],))


def unfire(db, wid):
    db.execute("UPDATE k_workers SET fired=NULL, active=1 WHERE id=?", (wid,))
    db.execute("UPDATE ready_workers SET active=1 WHERE kw_id=?", (wid,))
    import kassa as K
    ym = K.ym_today()
    K.ensure_month(ym)
    w = db.execute("SELECT salary FROM k_workers WHERE id=?", (wid,)).fetchone()
    mx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_ws WHERE ym=?", (ym,)).fetchone()[0]
    db.execute("INSERT OR IGNORE INTO k_ws(ym,worker_id,sort,salary) VALUES(?,?,?,?)", (ym, wid, mx + 10, w[0] if w else 0))


def register(app_module):
    global A
    A = app_module
    app, lr, ar = A.app, A.login_required, A.admin_required
    con = A.connect()
    try:
        init(con); con.commit()
    finally:
        con.close()
    from flask import request, render_template, redirect, url_for, flash, abort

    def back_to(default):
        b = request.values.get("back") or ""
        return b if b.startswith("/") and not b.startswith("//") else default

    @app.route("/staff")
    @lr
    def staff_list():
        show = request.args.get("show") if request.args.get("show") in ("on", "fired", "all") else "on"
        rows = []
        tg = {}
        for r in A.q("SELECT kw_id, role, active, bot_uid FROM ready_workers WHERE kw_id IS NOT NULL"):
            tg.setdefault(r["kw_id"], []).append(r)
        logins = {r["id"]: r for r in A.q("SELECT id, username, active FROM users")}
        cnt = dict(on=0, fired=0, svc=0, all=0)
        for w in A.q("SELECT * FROM k_workers ORDER BY name COLLATE NOCASE"):
            st = status(w)
            st = "fired" if st == "off" else st
            if st == "svc":          # ташқи хизмат (бошқа цех) — ходим эмас, рўйхатда кўрсатилмайди
                continue
            cnt[st] += 1
            cnt["all"] += 1
            if show != "all" and st != show:
                continue
            ph = tg.get(w["id"], [])
            rows.append(dict(w=w, st=status(w), bot=any(p["role"] in ("raspil", "staff") and p["active"] for p in ph),
                             inbot=any(p["bot_uid"] and p["active"] for p in ph), ready=any(p["active"] for p in ph),
                             login=logins.get(w["user_id"]) if w["user_id"] else None))
        loose = A.q("SELECT * FROM ready_workers WHERE kw_id IS NULL AND COALESCE(owner,0)=0 ORDER BY name") if A.is_admin() else []
        workers = A.q("SELECT id, name, fio FROM k_workers WHERE COALESCE(svc,0)=0 AND (fired IS NULL OR fired='') ORDER BY name COLLATE NOCASE")
        return render_template("staff_list.html", rows=rows, show=show, cnt=cnt, loose=loose, workers=workers)

    @app.route("/staff/loose", methods=["POST"])
    @ar
    def staff_loose():
        f, db = request.form, A.db()
        rid = int(A.parse_float(f.get("id"), 0))
        if f.get("action") == "link":
            kw = int(A.parse_float(f.get("kw"), 0))
            if kw and A.q("SELECT 1 FROM k_workers WHERE id=?", (kw,), one=True):
                db.execute("UPDATE ready_workers SET kw_id=? WHERE id=?", (kw, rid))
                db.commit()
                flash("Номер привязан к сотруднику", "ok")
        elif f.get("action") == "del":
            db.execute("DELETE FROM ready_workers WHERE id=? AND kw_id IS NULL AND COALESCE(owner,0)=0", (rid,))
            db.commit()
            flash("Номер удалён", "ok")
        return redirect(url_for("staff_list"))

    def paid_history(wid, ym, yms):
        """Ишчига берилган пуллар (ойлик/аванс) — ой бўйича ҳар бир тўлов: сана, нақд ёки карта (қайси ҳисобдан), сумма, кассадаги изоҳ.
        28.10 (эгаси): кунма-кун кассани очиб қарамаслик учун. ym бўш — тўлов бўлган охирги ой."""
        import kassa as K
        have = [r["ym"] for r in A.q("SELECT ym FROM k_cells WHERE kind='wage' AND ref_id=? AND (uzs<>0 OR usd<>0) "
                                     "UNION SELECT ym FROM k_noncash WHERE kind='wage' AND ref_id=? AND (uzs<>0 OR usd<>0) ORDER BY 1 DESC", (wid, wid))]
        opts = sorted(set(have) | set(yms), reverse=True)
        yp = K.ym_parse(ym) if ym else None
        ym = f"{yp[0]:04d}-{yp[1]:02d}" if yp else None
        if not ym:
            ym = have[0] if have else (opts[0] if opts else date.today().isoformat()[:7])
        rows = A.q("SELECT day, uzs, usd, TRIM(COALESCE(note_uzs,'') || CASE WHEN COALESCE(note_uzs,'')<>'' AND COALESCE(note_usd,'')<>'' "
                   "AND note_usd<>note_uzs THEN '; ' || note_usd WHEN COALESCE(note_uzs,'')='' THEN COALESCE(note_usd,'') ELSE '' END) note, '' acc "
                   "FROM k_cells WHERE ym=? AND kind='wage' AND ref_id=? AND (uzs<>0 OR usd<>0) "
                   "UNION ALL SELECT n.day, n.uzs, n.usd, COALESCE(n.note,''), COALESCE(a.name,'карта') FROM k_noncash n "
                   "LEFT JOIN erp_accounts a ON a.id=n.acc_id WHERE n.ym=? AND n.kind='wage' AND n.ref_id=? AND (n.uzs<>0 OR n.usd<>0) "
                   "ORDER BY 1", (ym, wid, ym, wid))
        out = [dict(day=r["day"], uzs=r["uzs"] or 0, usd=r["usd"] or 0, note=(r["note"] or "").strip(), acc=r["acc"] or "",
                    url=url_for("kassa_day", ym=ym, d=r["day"])) for r in rows]
        return dict(rows=out, ym=ym, opts=opts or [ym], uzs=sum(x["uzs"] for x in out), usd=sum(x["usd"] for x in out)), ym

    @app.route("/staff/<int:wid>", methods=["GET", "POST"])
    @lr
    def staff_card(wid):
        import raspil as P, ready as R, kassa as K
        w = A.q("SELECT * FROM k_workers WHERE id=?", (wid,), one=True) or abort(404)
        if request.method == "POST":
            if not A.is_admin():
                abort(403)
            f, db, act = request.form, A.db(), request.form.get("action")
            rid = int(A.parse_float(f.get("rid"), 0))
            mine = rid and A.q("SELECT 1 FROM ready_workers WHERE id=? AND kw_id=?", (rid, wid), one=True)
            if act == "fire":
                fd = _date(f.get("fired")) or date.today().isoformat()
                fire(db, wid, fd)
                flash(f"Уволен с {fd[8:10]}.{fd[5:7]}.{fd[:4]}. В табеле этого месяца остаётся, в следующий не переходит. Бот и вход отключены.", "ok")
            elif act == "unfire":
                unfire(db, wid)
                flash("Снова работает: добавлен в табель текущего месяца, бот включён", "ok")
            elif act == "addphone":
                ph = R.norm_phone(f.get("phone"))
                pr = PRESETS.get(f.get("preset")) or PRESETS["operator"]
                role, boss, staj, kroy, mgr = pr[2:]
                ex = A.q("SELECT * FROM ready_workers WHERE phone=?", (ph,), one=True) if ph else None
                if not ph:
                    flash("Неверный номер. Формат: +998 XX XXX XX XX", "error")
                elif ex and ex["kw_id"] and ex["kw_id"] != wid:
                    flash("Этот номер уже у другого сотрудника", "error")
                else:
                    if ex:
                        db.execute("UPDATE ready_workers SET kw_id=?, role=?, active=1 WHERE id=?", (wid, role, ex["id"]))
                        nid = ex["id"]
                    else:
                        nid = db.execute("INSERT INTO ready_workers(name, phone, created_at, role, kw_id) VALUES(?,?,?,?,?)",
                                         (w["name"], ph, A.now(), role, wid)).lastrowid
                    db.execute("UPDATE ready_workers SET boss=?, stajer=?, kroy=?, mgr=?, driver=? WHERE id=?",
                               (boss, staj, kroy, mgr, 1 if f.get("preset") == "driver" else 0, nid))
                    flash("Номер добавлен. " + ("Сотрудник открывает цех-бота и нажимает «Отправить мой номер»." if role != "ready"
                                                else "Через 30 секунд платформа начнёт читать его «тайёр»."), "ok")
            elif act == "preset" and mine:
                pr = PRESETS.get(f.get("preset"))
                if pr:
                    role, boss, staj, kroy, mgr = pr[2:]
                    db.execute("UPDATE ready_workers SET role=?, boss=?, stajer=?, kroy=?, mgr=?, driver=?" + (", bot_uid=NULL" if role == "ready" else "") + " WHERE id=?",
                               (role, boss, staj, kroy, mgr, 1 if f.get("preset") == "driver" else 0, rid))
                    flash(f"Сохранено: {pr[0]}", "ok")
            elif act == "role" and mine:
                role = f.get("role") if f.get("role") in ROLES else "raspil"
                db.execute("UPDATE ready_workers SET role=?" + (", bot_uid=NULL" if role == "ready" else "") + " WHERE id=?", (role, rid))
                flash("Сохранено", "ok")
            elif act in ("kroy", "mgr", "onoff") and mine:
                col = {"kroy": "kroy", "mgr": "mgr", "onoff": "active"}[act]
                db.execute(f"UPDATE ready_workers SET {col}=1-COALESCE({col},0) WHERE id=?", (rid,))
                flash("Сохранено", "ok")
            elif act == "perms" and mine:
                ps = [f"{m}:{r}" for m in P.MACHINES for r in ("op", "hp") if f.get(f"p_{m}_{r}")]
                db.execute("UPDATE ready_workers SET perms=?, stajer=?, boss=? WHERE id=?",
                           (json.dumps(ps), 1 if f.get("stajer") else 0, 1 if f.get("boss") else 0, rid))
                flash("Доступ сохранён", "ok")
            elif act == "delphone" and mine:
                if A.q("SELECT 1 FROM ready_workers WHERE id=? AND COALESCE(owner,0)=1", (rid,), one=True):
                    db.execute("UPDATE ready_workers SET kw_id=NULL, role='ready' WHERE id=?", (rid,))
                else:
                    db.execute("DELETE FROM ready_workers WHERE id=?", (rid,))
                flash("Номер удалён", "ok")
            elif act == "basis":
                fid = (f.get("fid") or "").strip()
                if fid:
                    try:
                        mp = json.loads(A.get_setting("basis_map", "") or "{}")
                    except ValueError:
                        mp = {}
                    mp[fid] = wid
                    A.set_settings(basis_map=json.dumps(mp))
                    try:
                        P.basis_sync(K.ym_today())
                    except Exception:
                        A.errlog.exception("basis sync")
                    flash("Сохранено: конструктор в 1С", "ok")
            elif act == "login":
                uid = int(A.parse_float(f.get("user_id"), 0)) or None
                if uid and A.q("SELECT 1 FROM k_workers WHERE user_id=? AND id<>?", (uid, wid), one=True):
                    flash("Этот вход уже привязан к другому сотруднику", "error")
                else:
                    db.execute("UPDATE k_workers SET user_id=? WHERE id=?", (uid, wid))
                    flash("Сохранено", "ok")
            db.commit()
            return redirect(url_for("staff_card", wid=wid, back=request.args.get("back")))
        # «Выдано» — кассадаги «Ойлик ва аванс» (нақд) + «Картадан / банкдан тўловлар» (28.10: картадан берилгани ҳам — табелдагидек)
        months = A.q("SELECT s.ym, s.salary, s.extra, s.total_override, "
                     "(SELECT COALESCE(SUM(uzs),0) FROM k_cells c WHERE c.ym=s.ym AND c.kind='wage' AND c.ref_id=s.worker_id) + "
                     "(SELECT COALESCE(SUM(uzs),0) FROM k_noncash n WHERE n.ym=s.ym AND n.kind='wage' AND n.ref_id=s.worker_id) paid_uzs, "
                     "(SELECT COALESCE(SUM(usd),0) FROM k_cells c WHERE c.ym=s.ym AND c.kind='wage' AND c.ref_id=s.worker_id) + "
                     "(SELECT COALESCE(SUM(usd),0) FROM k_noncash n WHERE n.ym=s.ym AND n.kind='wage' AND n.ref_id=s.worker_id) paid_usd "
                     "FROM k_ws s WHERE s.worker_id=? ORDER BY s.ym DESC LIMIT 12", (wid,))
        pays, pym = paid_history(wid, request.args.get("pym"), [m["ym"] for m in months])
        kpi = A.q("SELECT DISTINCT kind FROM k_kpi_cols WHERE worker_id=? AND active=1", (wid,))
        login = A.q("SELECT * FROM users WHERE id=?", (w["user_id"],), one=True) if w["user_id"] else None
        free = A.q("SELECT id, username, full_name, active FROM users WHERE id NOT IN (SELECT user_id FROM k_workers WHERE user_id IS NOT NULL) "
                   "ORDER BY active DESC, username") if A.is_admin() else []
        bmine, bdes = basis_of(wid)
        return render_template("staff_card.html", w=w, st=status(w), phones=phones_of(wid, bool(bmine)), bmine=bmine, bdes=bdes, months=months, BROLES=ROLES, PRESETS=PRESETS,
                               pays=pays, pym=pym,
                               kpi=[K.KPI_KINDS.get(r["kind"], r["kind"]) for r in kpi], login=login, free=free, MACHINES=P.MACHINES,
                               first=first_month(wid), mname=K.ym_title, today_s=date.today().isoformat(),
                               back=back_to(url_for("staff_list")))

    @app.route("/staff/new", methods=["GET", "POST"])
    @app.route("/staff/<int:wid>/edit", methods=["GET", "POST"])
    @ar
    def staff_form(wid=None):
        import kassa as K
        w = A.q("SELECT * FROM k_workers WHERE id=?", (wid,), one=True) if wid else None
        if wid and not w:
            abort(404)
        if request.method == "POST":
            f, db = request.form, A.db()
            name, fio = _clean(f.get("name"), 40), _clean(f.get("fio"), 120)
            svc = (w["svc"] or 0) if w else 0      # ташқи хизмат (ровер ва ҳ.к.) — бу ерда танланмайди
            err = ("Укажите имя" if not name else
                   "Укажите Ф.И.О. (фамилия, имя, отчество)" if not svc and not fio else
                   "Сотрудник с таким именем уже есть" if A.q("SELECT 1 FROM k_workers WHERE name=? AND id<>?", (name, wid or 0), one=True) else "")
            hired = _date(f.get("hired"))
            vals = dict(name=name, fio=fio, position=_clean(f.get("position"), 60), phone=_clean(f.get("phone"), 40),
                        hired=hired, note=_clean(f.get("note"), 500), cur="USD" if f.get("cur") == "USD" else "UZS",
                        salary=A.parse_float(f.get("salary")), svc=svc)
            if err:
                flash(err, "error")
                return render_template("staff_form.html", w=dict(w or {}, **vals), new=not wid, POSITIONS=POSITIONS,
                                       back=back_to(url_for("staff_card", wid=wid) if wid else url_for("staff_list")))
            if wid:
                db.execute("UPDATE k_workers SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=?", tuple(vals.values()) + (wid,))
                ym = K.ym_today()
                db.execute("UPDATE k_ws SET salary=? WHERE ym=? AND worker_id=?", (vals["salary"], ym, wid))
                flash("Сохранено", "ok")
            else:
                mx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_workers").fetchone()[0]
                wid = db.execute("INSERT INTO k_workers(" + ", ".join(vals) + ", sort, active) VALUES(" + ",".join("?" * len(vals)) + ",?,1)",
                                 tuple(vals.values()) + (mx + 10,)).lastrowid
                yms = {K.ym_today()}
                if hired and hired[:7] < K.ym_today() and A.q("SELECT 1 FROM k_months WHERE ym=?", (hired[:7],), one=True):
                    yms.add(hired[:7])
                for ym in sorted(yms):
                    K.ensure_month(ym)
                    smx = db.execute("SELECT COALESCE(MAX(sort),0) FROM k_ws WHERE ym=?", (ym,)).fetchone()[0]
                    db.execute("INSERT OR IGNORE INTO k_ws(ym,worker_id,sort,salary) VALUES(?,?,?,?)", (ym, wid, smx + 10, vals["salary"]))
                flash(f"{name} принят на работу и добавлен в табель", "ok")
            db.commit()
            return redirect(url_for("staff_card", wid=wid))
        return render_template("staff_form.html", w=w or dict(cur="UZS", hired=date.today().isoformat()), new=not wid, POSITIONS=POSITIONS,
                               back=back_to(url_for("staff_card", wid=wid) if wid else url_for("staff_list")))
