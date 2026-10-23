"""Операции → Связь и контакты: битта жойда ботлар, Telegram аккаунт, ходим бўлмаган контактлар (офис рақами, шериклар,
менежерлар), сайт ва ҳаволалар, бошқа уланишлар (1С, SMS, Click/Payme) ҳолати.

Маълумот ўз жойида қолади: контактлар — ready_workers (kw_id IS NULL), цех боти — raspil_bot_* созламалари, ҳаволалар — links
созламаси (JSON). Токенлар ҳеч қачон тўлиқ кўрсатилмайди."""
import json, re

A = None

# Контакт нима қилади: (номи, изоҳ)
DOES = {"ready": ("Сообщения «тайёр»", "Если с этого номера в офисный Telegram придёт «тайёр» с фото накладной, клиенту уйдёт «заказ готов»."),
        "owner": ("Отчёт (партнёр)", "В цех-боте «Отчёт»: касса, продажи за день, цех, должники; каждый день — итог дня."),
        "mgr": ("Уведомления менеджера", "В цех-боте: работы без накладной (Ц-коды) и уведомления по кускам.")}
LINK_KINDS = {"site": "Сайт", "instagram": "Instagram", "telegram": "Telegram-канал / группа", "bot": "Telegram-бот", "other": "Другое"}


def mask(tok):
    tok = tok or ""
    return (tok[:6] + "…" + tok[-4:]) if len(tok) > 12 else ("•••" if tok else "")


def links_list():
    try:
        v = json.loads(A.get_setting("links", "") or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def register(app_module):
    global A
    A = app_module
    app = A.app
    from flask import request, render_template, redirect, url_for, flash

    @app.route("/admin/connections", methods=["GET", "POST"])
    @A.admin_required
    def connections():
        import ready as R
        if request.method == "POST":
            f, db, act = request.form, A.db(), request.form.get("action")
            rid = int(A.parse_float(f.get("id"), 0))
            if act == "cadd":
                ph, nm = R.norm_phone(f.get("phone")), re.sub(r"\s+", " ", f.get("name") or "").strip()[:60]
                does = [k for k in DOES if f.get("d_" + k)]
                ex = A.q("SELECT * FROM ready_workers WHERE phone=?", (ph,), one=True) if ph else None
                if not ph or not nm:
                    flash("Нужны имя и номер. Формат: +998 XX XXX XX XX", "error")
                elif ex and ex["kw_id"]:
                    flash("Этот номер у сотрудника — меняйте его в карточке сотрудника", "error")
                else:
                    if ex:
                        nid = ex["id"]
                        db.execute("UPDATE ready_workers SET name=?, active=1 WHERE id=?", (nm, nid))
                    else:
                        nid = db.execute("INSERT INTO ready_workers(name, phone, created_at, role) VALUES(?,?,?,'ready')", (nm, ph, A.now())).lastrowid
                    db.execute("UPDATE ready_workers SET owner=?, mgr=?, role='ready', active=1 WHERE id=?",
                               (1 if "owner" in does else 0, 1 if "mgr" in does else 0, nid))
                    db.commit()
                    flash(f"{nm} — добавлен" + (". Пусть откроет цех-бота и нажмёт «Отправить мой номер»." if ("owner" in does or "mgr" in does) else ""), "ok")
            elif act == "cflag" and A.q("SELECT 1 FROM ready_workers WHERE id=? AND kw_id IS NULL", (rid,), one=True):
                col = {"owner": "owner", "mgr": "mgr", "on": "active"}.get(f.get("flag"))
                if col:
                    db.execute(f"UPDATE ready_workers SET {col}=1-COALESCE({col},0) WHERE id=?", (rid,))
                    db.commit()
                    flash("Сохранено", "ok")
            elif act == "cname" and A.q("SELECT 1 FROM ready_workers WHERE id=? AND kw_id IS NULL", (rid,), one=True):
                nm = re.sub(r"\s+", " ", f.get("name") or "").strip()[:60]
                if nm:
                    db.execute("UPDATE ready_workers SET name=? WHERE id=?", (nm, rid))
                    db.commit()
                    flash("Сохранено", "ok")
            elif act == "clink":
                kw = int(A.parse_float(f.get("kw"), 0))
                if kw and A.q("SELECT 1 FROM k_workers WHERE id=?", (kw,), one=True):
                    db.execute("UPDATE ready_workers SET kw_id=? WHERE id=? AND kw_id IS NULL", (kw, rid))
                    db.commit()
                    flash("Номер перенесён в карточку сотрудника", "ok")
            elif act == "cdel":
                db.execute("DELETE FROM ready_workers WHERE id=? AND kw_id IS NULL", (rid,))
                db.commit()
                flash("Контакт удалён", "ok")
            elif act == "owner_hour":
                v = (f.get("owner_hour") or "19:00").strip()[:5]
                if re.fullmatch(r"\d{1,2}:\d{2}", v):
                    A.set_settings(owner_hour=v)
                    flash("Сохранено", "ok")
            elif act == "ladd":
                url = (f.get("url") or "").strip()[:300]
                nm = (f.get("name") or "").strip()[:80]
                kind = f.get("kind") if f.get("kind") in LINK_KINDS else "other"
                if url.startswith("@"):
                    url = "https://t.me/" + url[1:]
                if not re.match(r"https?://", url):
                    flash("Ссылка должна начинаться с https:// (или @username)", "error")
                else:
                    ls = links_list()
                    ls.append(dict(kind=kind, name=nm or url, url=url, note=(f.get("note") or "").strip()[:200]))
                    A.set_settings(links=json.dumps(ls, ensure_ascii=False))
                    flash("Добавлено", "ok")
            elif act == "ldel":
                ls = links_list()
                i = int(A.parse_float(f.get("i"), -1))
                if 0 <= i < len(ls):
                    ls.pop(i)
                    A.set_settings(links=json.dumps(ls, ensure_ascii=False))
                    flash("Удалено", "ok")
            elif act == "bot":
                import raspil_bot
                if f.get("disconnect"):
                    A.set_settings(raspil_bot_token="", raspil_bot_username="", raspil_bot_error="")
                    flash("Цех-бот отключён", "ok")
                else:
                    ok, txt = raspil_bot.connect(f.get("token"))
                    flash(("Бот подключён: " if ok else "Не подключился: ") + txt, "ok" if ok else "error")
            elif act == "cbot":                       # клиентлар учун бот: токен улаш / алмаштириш
                import tg
                token = (f.get("token") or "").strip()
                if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
                    flash("Токен выглядит неверно. Скопируйте его целиком из сообщения @BotFather (вида 1234567890:AAH...)", "error")
                else:
                    try:
                        me = tg.get_me(token)
                        A.set_settings(tg_token=token, tg_bot_username=me.get("username", ""), tg_bot_name=me.get("first_name", ""),
                                       tg_poll_error="", tg_offset="0", bot_enabled="1")
                        flash(f"Бот @{me.get('username')} подключён и включён", "ok")
                    except tg.TgError as e:
                        if e.code == 0:
                            A.set_settings(tg_token=token, tg_offset="0", bot_enabled="1")
                            flash("Токен сохранён, но нет связи с Telegram — проверим автоматически, когда появится интернет", "warn")
                        else:
                            flash("Telegram не принял токен: " + e.human(), "error")
            elif act == "cbot_toggle":
                on = A.get_setting("bot_enabled") != "1"
                A.set_settings(bot_enabled="1" if on else "0")
                flash("Бот для клиентов " + ("включён" if on else "выключен"), "ok")
            elif act == "cbot_del":
                A.set_settings(tg_token="", tg_bot_username="", tg_bot_name="", tg_poll_error="", bot_enabled="0")
                flash("Бот для клиентов отключён", "ok")
            return redirect(url_for("connections") + (("#" + f.get("anchor")) if f.get("anchor") else ""))
        if A.get_setting("links_seeded", "") != "1":
            A.set_settings(links_seeded="1", links=json.dumps([
                dict(kind="site", name="Сайт Lumberture", url="https://lumberture.com", note="Тильда"),
                dict(kind="instagram", name="@lumberture.uz", url="https://instagram.com/lumberture.uz", note=""),
                dict(kind="telegram", name="Telegram-канал", url="https://t.me/Lumberture", note=""),
                dict(kind="bot", name="Бот для заказов @Lumberture_bot", url="https://t.me/Lumberture_bot", note="")], ensure_ascii=False))
        contacts = [dict(r) for r in A.q("SELECT * FROM ready_workers WHERE kw_id IS NULL ORDER BY active DESC, name COLLATE NOCASE")]
        workers = A.q("SELECT id, name, fio FROM k_workers WHERE COALESCE(svc,0)=0 AND (fired IS NULL OR fired='') ORDER BY name COLLATE NOCASE")
        st = {k: A.get_setting(k, "") for k in ("raspil_bot_token", "raspil_bot_username", "raspil_bot_error", "tg_token", "tg_bot_username",
                                                "bot_enabled", "tgu_phone", "tgu_me", "tgu_auth_error", "sms_provider", "c1_path",
                                                "epay_click_chat", "epay_payme_chat")}
        try:
            me = json.loads(st["tgu_me"] or "{}")
            st["tgu_me"] = " · ".join(x for x in (me.get("name", ""), ("@" + me["username"]) if me.get("username") else "") if x) if isinstance(me, dict) else st["tgu_me"]
        except ValueError:
            pass
        try:
            import c1, os
            p1 = c1.base_path()
            st["c1_path"], st["c1_ok"] = p1, os.path.isfile(os.path.join(p1, "1Cv7.DD"))
        except Exception:
            st["c1_ok"] = False
        nstaff = A.q("SELECT COUNT(*) n FROM ready_workers WHERE kw_id IS NOT NULL AND active=1", one=True)["n"]
        return render_template("connections.html", contacts=contacts, workers=workers, DOES=DOES, LINK_KINDS=LINK_KINDS, links=links_list(),
                               st=st, mask=mask, tgu=A.tgu_ready(), nstaff=nstaff, owner_hour=A.get_setting("owner_hour", "19:00"))
