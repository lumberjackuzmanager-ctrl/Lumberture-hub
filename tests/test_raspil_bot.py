"""Распил боти: рақам улаш, смена тугмалари, расм → тугмалар → ҳисоблаш, қисман, бекор."""
import os, sys, tempfile, json
from datetime import date, timedelta
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
fake = os.path.join(tmp, "fakeocr.py")
open(fake, "w", encoding="utf-8").write("import sys\nprint('')\n")
os.environ["RENT_OCR_CMD"] = f"{sys.executable} {fake}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, ready as R, tg, c1

sent = []
def fake_call(token, method, params=None, timeout=15):
    sent.append((method, params or {}))
    if method == "sendMessage":
        return {"message_id": 900 + len(sent)}
    if method == "getMe":
        return {"username": "lumb_raspil_bot"}
    if method == "getFile":
        return {"file_path": "photos/x.jpg"}
    return True
tg.call = fake_call
class FakeResp:
    content = b"jpg"
    def raise_for_status(self): pass
B.requests.get = lambda *a, **k: FakeResp()

L = lambda name, qty, price, unit="кол-в": dict(name=name, qty=qty, price_uzs=price, price=price, unit=unit)
docs = {"6241": "A1", "6242": "A2", "6243": "A3", "6244": "A4"}
lines_by = {"A1": [dict(L("ЛДСП Ультрадекор 5,0325 Кашемир 16мм (5981 BS)", 10, 900000, "лист"), no=1),
                   dict(L("ЛХДФ 2,5мм Белый (0101 PE)", 4, 300000, "лист"), no=2),
                   dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 6, 40000), no=3), dict(L("Распил ХДФ (Пакетный)", 4, 25000), no=4),
                   dict(L("Распил Кусок", 5, 11000, "кв.м"), no=5)],
            "A2": [dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 3, 40000), no=1)],
            "A3": [dict(L("Распил Л И С Т Т А Ш К А Р И Д А Н  (Пакетный)", 2, 50000), no=1, note="Кашемир 18мм Kronospan"),
                   dict(L("Распил Л И С Т Т А Ш К А Р И Д А Н  (Пакетный)", 1, 50000), no=2, note="")],
            "A4": [dict(L("ЛДСП Белый", 10, 900000, "лист"), no=1), dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв", 10, 25000), no=2)]}
def fake_pick(text, ocr):
    import re
    m = re.search(r"\d{3,6}", text or "")
    if m and m.group(0) in docs:
        return dict(iddoc=docs[m.group(0)], no=m.group(0), date=date.today()), "ok", ""
    return None, ("nodoc" if m else "unknown"), (m.group(0) if m else "")
R.pick_doc = fake_pick
R.doc_client = lambda iddoc: ("Мижоз " + iddoc, None)
c1.sale_doc = lambda iddoc: dict(lines=lines_by[iddoc])

def last(method="sendMessage"):
    return [p for m, p in sent if m == method][-1]
def kb_data(p):
    return [b["callback_data"] for row in p["reply_markup"]["inline_keyboard"] for b in row if b["callback_data"] != "home"]   # «🏠 Асосий меню» — ҳар клавиатурада
UID = 555
msg = lambda **k: dict(dict(message_id=k.pop("mid", 1), chat={"id": UID, "type": "private"}, **{"from": {"id": UID}}), **k)
cb = lambda data, mid=950: {"id": "q", "from": {"id": UID}, "data": data, "message": {"chat": {"id": UID}, "message_id": mid}}

with A.app.app_context():
    assert B.connect("123")[0] is False
    ok, txt = B.connect("123456:" + "A" * 30); assert ok and "lumb_raspil_bot" in txt, txt
    db = A.db()
    for nm in ("Жасур", "Улмас", "Огабек"):
        db.execute("INSERT INTO k_workers(name) VALUES(?)", (nm,))
    J, U, O = [r["id"] for r in A.q("SELECT id FROM k_workers ORDER BY id")]
    db.execute("INSERT INTO ready_workers(name,phone,role,kw_id,active,last_id) VALUES('Жасур','998901110000','raspil',?,1,1)", (J,))
    db.commit()
    # рўйхатда йўқ одам
    B.handle_update({"message": msg(text="/start")})
    assert "request_contact" in json.dumps(last()["reply_markup"])
    B.handle_update({"message": msg(contact={"phone_number": "+998 90 999 99 99", "user_id": UID})})
    assert "рўйхатда йўқ" in last()["text"]
    B.handle_update({"message": msg(contact={"phone_number": "+998901110000", "user_id": 777})})
    assert "Ўзингизнинг" in last()["text"]
    B.handle_update({"message": msg(contact={"phone_number": "998901110000", "user_id": UID})})
    assert "Салом, Жасур" in last()["text"] and last()["reply_markup"] == B._kb_add_lang(B._kb_add_pay(B.MAIN_KB))
    # смена: тугмалар
    B.handle_update({"message": msg(text=B.B_ARRA)})
    assert "jr:arra:op" in kb_data(last()) and "jr:arra:hp" in kb_data(last())
    B.handle_update({"callback_query": cb(f"op:arra:{J}")})
    e = last("editMessageText"); assert "Ёрдамчи" in e["text"] and f"hp:arra:{J}:0" in kb_data(e) and f"hp:arra:{J}:{U}" in kb_data(e)
    B.handle_update({"callback_query": cb(f"hp:arra:{J}:{U}")})
    e = last("editMessageText"); assert "Арра маркази" in e["text"] and "Улмас" in e["text"] and "расмини" in e["text"], e
    assert P.open_crews(kw=J)[0]["helper_id"] == U
    # расм → листлар тугма бўлиб
    B.handle_update({"message": msg(mid=10, photo=[{"file_id": "f1"}], caption="6241")})
    p = last(); d = kb_data(p)
    assert "№6241" in p["text"] and "Кашемир" in p["text"] and "Қайси листни" in p["text"], p["text"]
    pid = int(d[0].split(":")[1])
    assert f"i:{pid}:m1" in d and f"i:{pid}:m2" in d and f"j:{pid}:kus" in d, d
    btn = [b["text"] for r in p["reply_markup"]["inline_keyboard"] for b in r]
    assert any(t.startswith("1. Кашемир 16мм — 6 та") for t in btn) and any("ХДФ 2,5мм Белый — 4 та" in t for t in btn), btn                 # 10 сотилган, распил 6 → 6
    B.handle_update({"callback_query": cb(f"i:{pid}:m1")})
    e = last("editMessageText"); assert "Кесиляпти" in e["text"] and f"c:{pid}:m1" in kb_data(e)
    B.handle_update({"callback_query": cb(f"c:{pid}:m1")})
    e = last("editMessageText"); assert "Нечта" in e["text"] and f"q:{pid}:m1:6" in kb_data(e) and f"q:{pid}:m1:7" not in kb_data(e), kb_data(e)
    B.handle_update({"callback_query": cb(f"q:{pid}:m1:4")})
    e = last("editMessageText"); assert "4 та → 4 лист" in e["text"] and any(x.startswith("x:") for x in kb_data(e)), e["text"]
    assert any("Кашемир" in b["text"] and "— 2 та" in b["text"] for r in e["reply_markup"]["inline_keyboard"] for b in r)
    jid_k = A.q("SELECT id FROM raspil_job WHERE status='ok' ORDER BY id DESC", one=True)["id"]
    # чегарадан ошиб бўлмайди
    rwx = B.worker_by_uid(UID)
    assert P.record_item(rwx, "A1", "6241", "x", "m1", 3)[0] is None
    B.handle_update({"callback_query": cb(f"c:{pid}:m1")}); assert "рақамини ёзинг" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=7001, text="5")}); assert "кўпи билан 2" in last()["text"], last()
    B.handle_update({"message": msg(mid=7002, text="2")}); assert "2 та → 2 лист" in last("editMessageText")["text"], last("editMessageText")
    # ХДФ: 4 та → 2 лист; кусок — 1
    B.handle_update({"callback_query": cb(f"q:{pid}:m2:4")})
    assert "4 та → 2 лист" in last("editMessageText")["text"]
    B.handle_update({"callback_query": cb(f"j:{pid}:kus")})
    e = last("editMessageText"); assert "Кусок: 1 лист" in e["text"] and "Тўлиқ кесилган" in e["text"], e["text"]
    assert A.scalar("SELECT SUM(sheets) FROM raspil_job WHERE iddoc='A1' AND status='ok'") == 6 + 2 + 1
    # охиргисини бекор қилиш → яна танлаш мумкин
    B.handle_update({"callback_query": cb(f"x:{jid_k}")})
    e = last("editMessageText"); assert "бекор" in e["text"] and any("— 4 та" in b["text"] for r in e["reply_markup"]["inline_keyboard"] for b in r), e["text"]
    B.handle_update({"callback_query": cb(f"j:{pid}:close")})
    assert "Ҳисобланди: 5 лист" in last("editMessageText")["text"], last("editMessageText")["text"]
    # рақамсиз расм → рақам сўрайди → ёзса листлар (материал йўқ — распил сонидан)
    B.handle_update({"message": msg(mid=12, photo=[{"file_id": "f2"}])})
    assert "рақамини ёзинг" in last()["text"]
    B.handle_update({"message": msg(mid=13, text="6242")})
    d = kb_data(last()); assert "№6242" in last()["text"] and any(":rmain" in x for x in d), d
    # мижоз листи ва арзон распил
    B.handle_update({"message": msg(mid=14, text="6243")})
    d = kb_data(last()); assert any(x.endswith(":o1") for x in d) and any(x.endswith(":o2") for x in d), d
    assert "Мижоз листи: Кашемир 18мм Kronospan" in last()["text"] and "Мижоз листи (ташқаридан)" in last()["text"], last()["text"]
    B.handle_update({"message": msg(mid=15, text="6244")})
    pid4 = int(kb_data(last())[0].split(":")[1])
    B.handle_update({"callback_query": cb(f"q:{pid4}:m1:10")})
    assert "10 та → 5 лист" in last("editMessageText")["text"], last("editMessageText")["text"]
    # накладнойсиз: кесилди → кутилмоқда (ҳисобланмайди) → накладной расми → улаш
    lines_by["A5"] = [dict(L("ЛДСП Сатин", 5, 900000, "лист"), no=1), dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв (Пакетный)", 5, 40000), no=2)]
    docs["6245"] = "A5"
    before = A.scalar("SELECT COALESCE(SUM(sheets),0) FROM raspil_job WHERE status='ok'")
    B.handle_update({"message": msg(text=B.B_WAIT)})
    assert "w:k:main" in kb_data(last())
    B.handle_update({"callback_query": cb("w:k:main", mid=960)})
    B.handle_update({"callback_query": cb("w:n:main:6", mid=960)})
    assert "🔖 №" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=20, text="Алишер 90 123 45 67")})
    assert A.q("SELECT client FROM raspil_job WHERE status='wait'", one=True)["client"].startswith("Алишер")
    assert A.scalar("SELECT COALESCE(SUM(sheets),0) FROM raspil_job WHERE status='ok'") == before
    B.handle_update({"message": msg(mid=21, text="6245")})
    d = kb_data(last()); lk = [x for x in d if x.startswith("l:")]
    assert lk and "Накладнойсиз иш" in last()["text"], (last()["text"], d)
    B.handle_update({"callback_query": cb(lk[0])})
    e = last("editMessageText")["text"]; assert "уланди: 5 та" in e and "1 таси накладнойда йўқ" in e, e
    assert A.scalar("SELECT COALESCE(SUM(sheets),0) FROM raspil_job WHERE status='ok'") == before + 5
    assert A.q("SELECT status FROM raspil_job WHERE client LIKE 'Алишер%'", one=True)["status"] == "linked"
    # 6 лист сотилган, распил 5 — биттаси бутун: аввал қайси бири бутун кетишини белгилайди, кейин кесади
    lines_by["A7"] = [dict(L("ЛДСП Кашемир", 3, 900000, "лист"), no=1), dict(L("ЛДСП Белый", 3, 900000, "лист"), no=2),
                      dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв", 5, 40000), no=3)]
    docs["6246"] = "A7"
    # одатда ишчидан сўралмайди: кесганини босади, распил чегарасига етгач қолгани ўзи «бутун»
    B.handle_update({"message": msg(mid=22, text="6246")})
    p = last(); assert not any(x.startswith("bw:") for x in kb_data(p)) and "бутун кетади" not in p["text"], p["text"]
    k1 = [x for x in kb_data(p) if x.startswith("i:") and x.endswith(":m1")][0]
    assert P.record_item(B.worker_by_uid(UID), "A7", "6246", "x", "m1", 3)[0] and P.record_item(B.worker_by_uid(UID), "A7", "6246", "x", "m2", 2)[0]
    assert P.record_item(B.worker_by_uid(UID), "A7", "6246", "x", "m2", 1)[0] is None                   # 6-лист ҳисобланмайди
    B.handle_update({"message": msg(mid=23, text="6246")}); assert "1 таси бутун" in last()["text"], last()["text"]
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE iddoc='A7'"); A.db().commit()
    A.set_settings(raspil_ask_whole="1")
    B.handle_update({"message": msg(mid=22, text="6246")})
    p = last(); d = kb_data(p)
    assert "1 таси бутун кетади" in p["text"] and any(x.startswith("bw:") for x in d) and not any(x.startswith("i:") for x in d), (p["text"], d)
    assert P.record_item(B.worker_by_uid(UID), "A7", "6246", "x", "m1", 3)[0] is None               # белгиламасдан кесиб бўлмайди
    B.handle_update({"callback_query": cb([x for x in d if x.startswith("bw:") and x.endswith(":m2")][0])})
    e = last("editMessageText"); d = kb_data(e)
    assert "1 таси бутун" in e["text"] and "Белый — 2 та" in json.dumps(e["reply_markup"], ensure_ascii=False) and "Кашемир — 3 та" in json.dumps(e["reply_markup"], ensure_ascii=False), e
    st7 = P.items_state("A7"); assert sum(i["left"] for i in st7["items"]) == 5 and not st7["need"].get("main"), st7
    B.handle_update({"callback_query": cb([x for x in d if x.startswith("bwx:")][0])})              # бекор — яна сўрайди
    assert P.items_state("A7")["need"]["main"] == 1
    P.mark_whole(B.worker_by_uid(UID), "A7", "m2")
    A.set_settings(raspil_ask_whole="0")
    # админ тасдиқлаши (мижоз товол қилган)
    rwx = B.worker_by_uid(UID)
    jw = P.add_wait(rwx, "hdf", 4, "товол")
    ok, t = P.approve_wait(jw, by="admin"); assert ok and "2 лист" in t, t
    # телефон бўйича ўзи уланади: накладной кейин ёзилган, мижознинг 1С'даги рақами мос
    lines_by["A6"] = [dict(L("ЛДСП Сатин", 3, 900000, "лист"), no=1), dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв", 3, 40000), no=2)]
    ja = P.add_wait(rwx, "main", 3, "Бобур 93 555 11 22")
    jb = P.add_wait(rwx, "main", 2, "Бошқа 99 000 00 01")
    _cl, _tb, _jr = c1.clients, c1.table, c1.journal
    c1.clients = lambda: {"C1": dict(name="Бобур ака", phones="93 555-11-22"), "C2": dict(name="X", phones="90 111-11-11")}
    c1.table = lambda name: [dict(IDDOC="A6", SP232="C1"), dict(IDDOC="A2", SP232="C2")] if name == "DH210" else []
    c1.journal = lambda: {"A6": dict(kind=210, posted=True, deleted=False, date=date.today(), no="6301"),
                          "A2": dict(kind=210, posted=True, deleted=False, date=date.today(), no="6302")}
    assert P.auto_link_waits() == 1
    assert A.q("SELECT status, doc_no FROM raspil_job WHERE id=?", (ja,), one=True)["doc_no"] == "6301"
    assert A.q("SELECT status FROM raspil_job WHERE id=?", (jb,), one=True)["status"] == "wait"
    # код бўйича: менежер 1С изоҳига «Ц<код>» ёзди → ўзи уланади, ишчига «тўланди»
    jc = P.add_wait(rwx, "main", 2, "35/1 серый")
    code = P.wait_code(jc); assert code
    lines_by["A8"] = [dict(L("ЛДСП Сатин", 2, 900000, "лист"), no=1, note=f"ц-{code} цехдан"), dict(L("Распил ЛДСП/ЛМДФ 4,5 кв - 5 кв", 2, 40000), no=2)]
    _sn = c1.sale_note1
    c1.sale_note1 = lambda iddoc: ""
    c1.table = lambda name: ([dict(IDDOC="A8", SP232="C2")] if name == "DH210" else
                             [dict(IDDOC="A8", SP910=f"ц-{code} цехдан", SP1531="")] if name == "DT210" else [])
    c1.journal = lambda: {"A8": dict(kind=210, posted=True, deleted=False, date=date.today(), no="6303")}
    sent.clear()
    assert P.auto_link_waits() >= 1
    assert A.q("SELECT doc_no FROM raspil_job WHERE id=?", (jc,), one=True)["doc_no"] == "6303"
    assert any(P.clabel(code) in pp.get("text", "") and "тўланди" in pp.get("text", "") for m, pp in sent if m == "sendMessage"), sent
    assert not P._code_re(code).search(f"Ц{code}5") and P._code_re(code).search(f"№6303 Ц {code}")
    assert all(P._code_re(code).search(f"{x}{code}") for x in ("C", "c", "S", "s", "С", "ц")) and not P._code_re(code).search(f"AC{code}")
    B.handle_update({"message": msg(mid=990, text=f"ц{code}")}); assert "тўланган" in last()["text"], last()["text"]
    jx = P.add_wait(rwx, "main", 1, "x"); B.handle_update({"message": msg(mid=991, text=f"Ц{P.wait_code(jx)}")})
    assert "тўланмаган" in last()["text"] and "берманг" in last()["text"], last()["text"]
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE id=?", (jx,)); A.db().commit()
    c1.sale_note1 = _sn
    c1.clients, c1.table, c1.journal = _cl, _tb, _jr
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE id=?", (jb,)); A.db().commit()
    B.handle_update({"message": msg(text=B.B_TODAY)})
    # бугун ва иш тугади
    B.handle_update({"message": msg(text=B.B_TODAY)}); assert "Лист: 22" in last()["text"], last()["text"]
    B.handle_update({"message": msg(text=B.B_END)}); assert "иш тугади" in last()["text"]
    # содда клавиатура: 4 тугма; эртага «Ишга келдим» → «Кечагидек …» битта босиш
    assert sum(len(r) for r in B.MAIN_KB["keyboard"]) == 8
    B.handle_update({"message": msg(text=B.B_SWITCH)}); assert "sw:m:kromka" in kb_data(last()) and "sw:open" not in kb_data(last())
    B.handle_update({"message": msg(text=B.B_IN)}); d = kb_data(last())
    assert "Кечагидек" in json.dumps(last()["reply_markup"], ensure_ascii=False) and any(x.startswith("jr:") for x in d) and "sw:menu" in d, d
    B.handle_update({"callback_query": cb([x for x in d if x.startswith("jr:")][0])}); assert P.crew_for(B.worker_by_uid(UID))
    B.handle_update({"message": msg(text=B.B_END)})
    # ҳар ким ўзи киради: бир станокда битта бош оператор
    _rA = dict(id=9901, kw_id=U, name="Улмас"); _rB = dict(id=9902, kw_id=O, name="Огабек")
    ok, tx = P.join_crew(_rA, "oval", "op"); assert ok and "Бош оператор: Улмас" in tx, tx
    ok, tx = P.join_crew(_rB, "oval", "op"); assert not ok and "бош оператор бор" in tx, tx
    ok, tx = P.join_crew(_rB, "oval", "hp"); assert ok and "Ёрдамчи: Огабек" in tx and "Улмас" in tx, tx
    ok, tx = P.join_crew(_rA, "utol", "op"); assert ok                      # Улмас утолщенкага ўтди
    ov = [c for c in P.open_crews() if c["machine"] == "oval"]; assert ov and ov[0]["helper_id"] == O and not ov[0]["op_id"], ov
    ok, tx = P.join_crew(_rB, "oval", "op"); assert ok and "Бош оператор: Огабек" in tx, tx  # ёрдамчи бош операторга
    P.end_crew(_rB); assert not [c for c in P.open_crews() if c["machine"] == "oval"]
    assert [c for c in P.open_crews() if c["machine"] == "utol" and c["op_id"] == U]
    P.end_crew(_rA)
    # доступ: платформада белгиланади — фақат рухсат берилган станок/рол
    A.db().execute("INSERT INTO ready_workers(id, name, phone, created_at, role, kw_id, perms) VALUES(9903,'Огабек','998900009903',?, 'raspil', ?, ?)",
                   (A.now(), O, json.dumps(["kromka:hp"]))); A.db().commit()
    _rC = dict(A.q("SELECT * FROM ready_workers WHERE id=9903", one=True))
    ok, tx = P.join_crew(_rC, "kromka", "op"); assert not ok and "доступи берилмаган" in tx, tx
    ok, tx = P.join_crew(_rC, "arra", "hp"); assert not ok
    ok, tx = P.join_crew(_rC, "kromka", "hp"); assert ok, tx
    kbm = json.dumps(B.switch_kb(_rC), ensure_ascii=False); assert "sw:m:kromka" in kbm and "sw:m:arra" not in kbm, kbm
    rt, rk = B.role_kb("kromka", _rC); assert "jr:kromka:hp" in json.dumps(rk) and "jr:kromka:op" not in json.dumps(rk)
    P.end_crew(_rC)
    A.db().execute("UPDATE ready_workers SET stajer=1 WHERE id=9903"); A.db().commit()
    ok, tx = P.join_crew(_rC, "kromka", "hp"); assert not ok and "стажер" in tx, tx
    A.db().execute("UPDATE ready_workers SET stajer=0, boss=1, perms='[]' WHERE id=9903"); A.db().commit()
    ok, tx = P.join_crew(_rC, "arra", "op"); assert ok, tx                                  # цех бошлиғи — ҳаммаси
    P.end_crew(_rC)
    # менежер крой: бот → № → ЛДСП м² → ХДФ м²; синов ойида KPI'га ёзилмайди
    A.db().execute("INSERT INTO ready_workers(id, name, phone, created_at, role, kw_id, kroy, bot_uid) VALUES(9904,'Нодир','998900009904',?, 'staff', ?, 1, 7774)",
                   (A.now(), O)); A.db().commit()
    km = lambda **k: dict(msg(**k), **{"from": {"id": 7774}, "chat": {"id": 7774, "type": "private"}})
    B.handle_update({"message": km(text=B.B_IN)}); assert not any(B.B_KROY == b["text"] for r in last()["reply_markup"]["keyboard"] for b in r), last()
    B.handle_update({"message": km(text=B.B_KROY)}); assert "Накладной рақами" not in (last().get("text") or "")      # ботда крой йўқ
    _kid = P.kroy_add(dict(id=9904, kw_id=O), "6262", 11.592, 2.898)                                   # эски (бот) ёзуви — ҳисобда қолади
    A.set_settings(kroy_rate_main="20000", kroy_rate_hdf="5000", kroy_from="2099-01")
    o = P.kroy_month(date.today().isoformat()[:7])[O]; assert o["docs"] == 1 and round(o["pay"]) == 42500, o
    assert not A.q("SELECT 1 FROM k_kpi_cols WHERE worker_id=? AND kind='kroy'", (O,), one=True)
    _af = A.get_setting("auto_from", ""); A.set_settings(kroy_from="2000-01", auto_from="2000-01"); P.kroy_sync(date.today().isoformat()[:7])
    assert A.q("SELECT SUM(k.qty) s FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id WHERE c.worker_id=? AND c.kind='kroy'", (O,), one=True)["s"] == 2
    A.db().execute("UPDATE raspil_kroy SET status='del' WHERE id=?", (_kid,)); A.db().commit()
    assert not P.kroy_month(date.today().isoformat()[:7]).get(O)
    A.set_settings(kroy_from="2026-12", auto_from=_af or "2026-11")
    # кусок ҳамма учун: «фақат келди-кетди» ишчи ҳам омборга киритади ва сотади; ким киритди / ким сотди
    _kt3 = P.kusok_types; P.kusok_types = lambda: [("KL", "ЛДСП кусок")]
    import c1 as _c1; _gd3 = _c1.goods; _c1.goods = lambda: {"KL": dict(name="ЛДСП кусок")}
    A.db().execute("INSERT INTO ready_workers(id, name, phone, created_at, role, kw_id, bot_uid) VALUES(9905,'Офисчи','998900009905',?, 'staff', ?, 7775)",
                   (A.now(), U)); A.db().commit()
    ks_ = lambda **k: dict(msg(**k), **{"from": {"id": 7775}, "chat": {"id": 7775, "type": "private"}})
    kc_ = lambda d, mid=940: dict(cb(d, mid=mid), **{"from": {"id": 7775}})
    B.handle_update({"message": ks_(text=B.B_KUS)}); assert "sw:kin" in kb_data(last())
    B.handle_update({"callback_query": kc_("sw:kin")}); B.handle_update({"callback_query": kc_([x for x in kb_data(last("editMessageText")) if x.endswith(":KL")][0])})
    B.handle_update({"message": ks_(text="1500 600 венге")}); assert "Кимники" in last()["text"], last()["text"]
    B.handle_update({"message": ks_(text="райгаз")}); assert "Омборга қўшилди" in last()["text"], last()["text"]
    kk_ = [k for k in P.kusok_stock("KL") if k["color"] == "венге"][0]; assert kk_["who"] == P.wname(U), kk_
    pg_ = P.kusok_page(A.now()[:7]); assert any(w == P.wname(U) for w, v in pg_["byin"]), pg_["byin"]
    A.db().execute("UPDATE raspil_kusok SET status='del' WHERE id=?", (kk_["id"],)); A.db().commit()
    P.kusok_types, _c1.goods = _kt3, _gd3
    # бегона одамнинг тугмаси ишламайди
    sent.clear()
    B.handle_update({"callback_query": dict(cb(f"q:{pid}:m1:1"), **{"from": {"id": 1}})})
    assert last("answerCallbackQuery")["text"].startswith("Аввал")
with A.app.app_context():
    # присадка: тешик дона, эксцентрик ×2, паз 4мм м→дона, ЧПУ ва подсветка паз — 10%
    lines_by["P1"] = [dict(L("Услуга сверления Евростяжка (лицевая 5*7)", 10, 1500, "шт"), no=1, uzs=15000),
                      dict(L("Услуга сверления Под эксцентрики", 100, 2100, "шт"), no=2, uzs=210000),
                      dict(L("Услуга сверления Паз (4мм)", 5, 4000, "пог.м"), no=3, uzs=20000),
                      dict(L("Услуга фрезеровка  (обработка ЧПУ)", 2, 10000, "шт"), no=4, uzs=20000),
                      dict(L("Услуга сверления Паз (под подсветки)", 2, 15000, "пог.м"), no=5, uzs=30000),
                      dict(L("Услуга конструктора СТАНДАРТ", 3, 15000, "кв-м"), no=6, uzs=45000)]
    docs["7001"] = "P1"
    pc, ps, det = P.pris_calc(lines_by["P1"]); assert pc == 10 + 200 + 5 and ps == 50000, (pc, ps)
    B.handle_update({"message": msg(text=B.B_PRIS)}); assert "Присадка" in last()["text"] and "jr:pris:op" in kb_data(last())
    B.handle_update({"callback_query": cb(f"op:pris:{J}")}); B.handle_update({"callback_query": cb(f"hp:pris:{J}:0")})
    assert "Присадка" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=40, text="7001")})
    p = last(); assert "215 тешик" in p["text"] and "×2 = 200 тешик" in p["text"] and any(x.endswith(":pris") for x in kb_data(p)), p["text"]
    assert "сўм" not in p["text"] and "ЧПУ/паз 4" in p["text"], p["text"]                     # ишчига пул кўрсатилмайди
    pid7 = int(kb_data(p)[0].split(":")[1])
    B.handle_update({"callback_query": cb(f"pf:{pid7}:50")})                                        # бугун 50%
    e = last("editMessageText")["text"]; assert "107.5 тешик" in e and "Қолди 50%" in e and "сўм" not in e, e
    B.handle_update({"message": msg(mid=41, text="7001")})                                          # эртасига
    p = last(); assert "Олдин 50%" in p["text"] and "Қолгани тугади" in json.dumps(p["reply_markup"], ensure_ascii=False), p["text"]
    assert f"pf:{int(kb_data(p)[0].split(':')[1])}:75" not in kb_data(p)
    B.handle_update({"callback_query": cb(kb_data(p)[0])})
    assert "Тўлиқ бажарилди" in last("editMessageText")["text"], last("editMessageText")["text"]
    assert P.record_pris(B.worker_by_uid(UID), "P1", "7001", "x")[0] is None                  # такрор йўқ
    A.set_settings(raspil_kpi="1", auto_from="2000-01"); P.sync_kpi(date.today().isoformat())
    q = {(r["kind"]): (r["qty"], r["rate"]) for r in A.q("SELECT c.kind, k.qty, m.rate FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id "
                                                        "JOIN k_kpi_mcols m ON m.col_id=c.id AND m.ym=k.ym WHERE c.worker_id=?", (J,))}
    assert q["prisadka"] == (215, 140) and q["prisadka_pct"] == (50000, 0.1), q
    B.handle_update({"message": msg(text=B.B_TODAY)}); assert "215 тешик" in last()["text"]
    B.handle_update({"message": msg(text=B.B_SAL)}); st_ = last()["text"]
    assert "Ойлик" in st_ or "Жами ҳисобланган" in st_, st_
    A.set_settings(auto_from="2099-01"); B.handle_update({"message": msg(text=B.B_SAL)})
    assert "қўлда ҳисобланади" in last()["text"] and "2099" in last()["text"], last()["text"]
    A.set_settings(auto_from="2000-01")
    assert "215 тешик" in st_ and "ЧПУ/паз 4" in st_, st_
    mt = P.machine_totals(date.today().isoformat())
    assert {t["machine"] for t in mt} >= {"arra", "pris"} and any(t["machine"] == "pris" and t["pp"] == 215 for t in mt), mt
    # кромка (NB536), ровер, утолщенка, овал — 1С накладнойдан метраж / %
    lines_by["K1"] = [dict(L("Кромка ПВХ Белый Гладкий 19/1", 40, 3000, "пог.м"), no=1, uzs=120000),
                      dict(L("Услуга ПВХ от 19/04 до 22/2", 10, 2000, "пог.м"), no=2, uzs=20000),
                      dict(L("Клей прямой Хенкел ПУР", 1, 90000, "кг"), no=3, uzs=90000),
                      dict(L("Услуга Ровер", 3, 100000, "шт"), no=4, uzs=300000),
                      dict(L("Услуга обманка (клей)", 6, 4000, "пог.м"), no=5, uzs=24000),
                      dict(L("Кромка 19/1 ПВХ", 7, 6900, "пог.м"), no=6, uzs=48300, gid="OV1")]
    _rn = P._root_name; P._root_name = lambda gid: "3 Кромка Овал" if gid == "OV1" else ""
    docs["7002"] = "K1"
    assert P.work_calc("kromka", lines_by["K1"])[0] == 50 and P.work_calc("oval", lines_by["K1"])[0] == 7
    assert P.work_calc("utol", lines_by["K1"])[0] == 6 and P.work_calc("rover", lines_by["K1"])[:2] == (3, 300000)
    A.set_settings(kromka_rate="250", rover_pct="10")
    for btn, mach, want, done_q in ((B.B_KROMKA, "kromka", "Жами: 50 м", "50 м"),):
        B.handle_update({"message": msg(text=btn)})
        B.handle_update({"callback_query": cb(f"op:{mach}:{J}")}); B.handle_update({"callback_query": cb(f"hp:{mach}:{J}:0")})
        B.handle_update({"message": msg(mid=50, text="7002")})
        p = last(); assert want in p["text"] and "сўм" not in p["text"] and not any(x.startswith("pf:") for x in kb_data(p)), p["text"]
        B.handle_update({"callback_query": cb(([x for x in kb_data(p) if x.endswith(":pris")] or kb_data(p))[0])})
        e = last("editMessageText")["text"]; assert done_q in e and "ўлиқ" in e, e
    # ровер: накладнойда ровер хизмати бор — аввалгидек (сумма сўралмайди)
    B.handle_update({"message": msg(text=B.B_ROVER)})
    B.handle_update({"callback_query": cb(f"op:rover:{J}")}); B.handle_update({"callback_query": cb(f"hp:rover:{J}:0")})
    B.handle_update({"message": msg(mid=50, text="7002")})
    p = last(); assert "Жами: 3 шт" in p["text"] and "сўм" not in p["text"], p["text"]
    B.handle_update({"callback_query": cb(([x for x in kb_data(p) if x.endswith(":pris")] or kb_data(p))[0])})
    assert "3 шт" in last("editMessageText")["text"]
    # ровер: накладнойда ровер хизмати йўқ (6262) → сумма + изоҳ → менежерга; 1С'да «6262» изоҳли янги накладной → ойликка
    lines_by["N62"] = [dict(L("ЛДСП Белый", 2, 900000, "лист"), no=1)]
    docs["6262"] = "N62"
    B.handle_update({"message": msg(mid=54, text="6262")})
    p = last(); assert "ровер хизмати йўқ" in p["text"] and "суммаси" in p["text"], p["text"]
    B.handle_update({"message": msg(mid=55, text="150 000")}); assert "Нима қилдингиз" in last()["text"], last()["text"]
    B.handle_update({"message": msg(mid=56, text="2 та фасад фрезер")}); assert "менежерга юборилди" in last()["text"], last()["text"]
    jw = A.q("SELECT * FROM raspil_job WHERE machine='rover' AND status='wait' ORDER BY id DESC LIMIT 1", one=True)
    cc = P.code_card(jw["wcode"]); assert "№6262" in cc and "150000 сўм" in cc and "2 та фасад фрезер" in cc and "изоҳига «6262»" in cc, cc
    lines_by["N70"] = [dict(L("Услуга Ровер", 2, 90000, "шт"), no=1, uzs=180000)]
    _cl2, _tb2, _jr2, _sn2 = c1.clients, c1.table, c1.journal, c1.sale_note1
    c1.clients = lambda: {}; c1.sale_note1 = lambda iddoc: "накладной 6262 ровер" if iddoc == "N70" else ""
    c1.table = lambda name: []
    c1.journal = lambda: {"N70": dict(kind=210, posted=True, deleted=False, date=date.today(), no="7070"),
                          "N62": dict(kind=210, posted=True, deleted=False, date=date.today(), no="6262")}
    sent.clear()
    assert P.auto_link_waits() >= 1
    rj = A.q("SELECT * FROM raspil_job WHERE iddoc='N70' AND machine='rover' AND status='ok'", one=True)
    assert rj and rj["pr_sum"] == 180000 and rj["text"] == "2 та фасад фрезер" and rj["op_id"] == J, rj and dict(rj)
    assert any("№6262 ровер иши ҳисобланди" in pp.get("text", "") for m, pp in sent if m == "sendMessage"), sent
    c1.clients, c1.table, c1.journal, c1.sale_note1 = _cl2, _tb2, _jr2, _sn2
    P._root_name = _rn
    # накладнойсиз кромка: ишчи кромкани 1С рўйхатидан танлайди (ёзмайди), метрни ёзади
    _kt, _kp = P.kromka_tree, P.item_popularity
    P.kromka_tree = lambda m="kromka": ({"R": dict(name="2 Кромка", parent="", kids=["F"], items=[]),
                                        "F": dict(name="Кромка Huali 19/1", parent="R", kids=[], items=[("G1", "Кромка ПВХ Кашемир 19/1 (Huali)"), ("G2", "Кромка ПВХ Белый 19/1 (Huali)")])}, ["R"])
    P.item_popularity = lambda days=180: {"G1": 5}
    import c1 as _c1
    _gd = _c1.goods; _c1.goods = lambda: {"G1": dict(name="Кромка ПВХ Кашемир 19/1 (Huali)"), "G2": dict(name="Кромка ПВХ Белый 19/1 (Huali)")}
    B.handle_update({"callback_query": cb(f"op:kromka:{J}")}); B.handle_update({"callback_query": cb(f"hp:kromka:{J}:0")})
    B.handle_update({"callback_query": cb("sw:wait", mid=980)})
    d0 = kb_data(last("editMessageText")); assert d0[0] == "w:m:kromka" and "Қайси станок" in last("editMessageText")["text"], d0
    B.handle_update({"callback_query": cb("w:m:kromka", mid=980)})
    assert "w:c:kromka:0" in kb_data(last("editMessageText"))
    B.handle_update({"callback_query": cb("w:c:kromka:0", mid=980)}); assert "Мижоз исми" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=3979, text="Алишер")}); assert "Исм ва телефон" in last()["text"]
    B.handle_update({"message": msg(mid=3978, text="Алишер +998 90 123 11 31")})
    _cz = max(r["code"] for r in A.q("SELECT code FROM raspil_wcode"))
    assert P.code_client(_cz) == "Алишер 901231131" and P.parse_client("Вали 1131") == "Вали 1131" and P.parse_client("1131") is None
    e = last("editMessageText"); assert P.clabel(_cz) in e["text"] and any(x.startswith("ki:") for x in kb_data(e)), e
    B.handle_update({"callback_query": cb("w:c:kromka:0", mid=980)}); B.handle_update({"message": msg(mid=3980, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); d = kb_data(e)
    assert "🔖 №" in e["text"] and any(x.startswith("ki:") and x.endswith(":G1") for x in d) and any(x.startswith("kf:") for x in d), (e["text"], d)
    B.handle_update({"message": msg(mid=981, text="бел")}); d2 = kb_data(last())
    assert any(x.endswith(":G2") for x in d2), d2
    B.handle_update({"callback_query": cb([x for x in d if x.endswith(":G1")][0], mid=980)})
    assert "Неча метр" in last("editMessageText")["text"]
    assert P.parse_area("200-500") == (0.1, "200×500") and P.parse_area("2800 400 2")[0] == 2.24
    assert P.parse_area("200x500\n300 400") [0] == 0.22 and P.parse_area("2,8х0,4")[0] == 1.12 and P.parse_area("1.5") == (1.5, "")
    B.handle_update({"message": msg(mid=982, text="3,5")})
    m3 = last(); assert "Кашемир 19/1 — 3.5 м" in m3["text"] and "№" in m3["text"], m3["text"]
    B.handle_update({"callback_query": cb([x for x in kb_data(m3) if x.startswith("kd:")][0], mid=983)})
    jk = A.q("SELECT * FROM raspil_job WHERE status='wait' ORDER BY id DESC LIMIT 1", one=True)
    assert "Кашемир" in jk["text"] and "3.5 м" in jk["text"], dict(jk)
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE id=?", (jk["id"],)); A.db().commit()
    # овал кромка: размер (1С) → ранги (ишчи ёзади, мажбурий) → метр
    B.handle_update({"callback_query": cb("w:m:oval", mid=984)}); B.handle_update({"callback_query": cb("w:c:oval:0", mid=984)})
    B.handle_update({"message": msg(mid=3984, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); assert "Овал кромка — размерини" in e["text"], e
    B.handle_update({"callback_query": cb([x for x in kb_data(e) if x.endswith(":G1")][0], mid=984)})
    assert "Кромка ранги" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=985, text="5")}); assert "рангини ёзинг" in last()["text"]
    B.handle_update({"message": msg(mid=986, text="оқ глянец")}); assert "🎨 оқ глянец" in last()["text"] and "Неча метр" in last()["text"], last()
    B.handle_update({"message": msg(mid=987, text="4")}); mo = last(); assert "· оқ глянец — 4 м" in mo["text"], mo["text"]
    jo = A.q("SELECT * FROM raspil_job WHERE status='wait' ORDER BY id DESC LIMIT 1", one=True); assert jo["machine"] == "oval" and "оқ глянец" in jo["text"]
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE id=?", (jo["id"],)); A.db().commit()
    # накладнойсиз иш присадка/кромкада: сони сўралмайди, накладнойга уланганда шу станок иши тўлиқ ҳисобланади
    for mach, doc, lns in (("pris", "P2", [dict(L("Услуга сверления Под петли (35мм)", 12, 850, "шт"), no=1, uzs=10200)]),
                           ("kromka", "K3", [dict(L("Кромка ПВХ Капучино 19/1", 15, 4500, "пог.м"), no=1, uzs=67500)])):
        lines_by[doc] = lns
        B.handle_update({"callback_query": cb(f"op:{mach}:{J}")}); B.handle_update({"callback_query": cb(f"hp:{mach}:{J}:0")})
        B.handle_update({"callback_query": cb("sw:wait", mid=970)})
        B.handle_update({"callback_query": cb(f"w:m:{mach}", mid=970)})
        B.handle_update({"callback_query": cb(f"w:c:{mach}:0", mid=970)}); B.handle_update({"message": msg(mid=969, text="Тест 937776655")})
        e = last("editMessageText"); assert "🔖 №" in e["text"] and ("нима қилдингиз" in e["text"].lower() or "Кромка" in e["text"]), e
        B.handle_update({"message": msg(mid=971, text="93 777 66 55 Тест")})                     # телефони ёзилди
        jw2 = A.q("SELECT id FROM raspil_job WHERE status='wait' ORDER BY id DESC LIMIT 1", one=True)["id"]
        assert not P.approve_wait(jw2)[0]
        ok, t = P.link_wait(jw2, doc, "70" + doc, "Мижоз"); assert ok, t
        jj = A.q("SELECT * FROM raspil_job WHERE iddoc=? AND status='ok'", (doc,), one=True)
        assert jj["machine"] == mach and jj["pr_pcs"] == (12 if mach == "pris" else 15) and jj["op_id"] == J, dict(jj)
    P.kromka_tree, P.item_popularity, _c1.goods = _kt, _kp, _gd
    # битта мижоз — бир неча станок: двухпилка (кусок) → кромка → бир код Ц, битта накладной ҳаммасини улайди
    jd = P.add_wait(rwx, "job", 0, "кусок", machine="dp")
    cz = P.wait_code(jd)
    B.handle_update({"callback_query": cb("w:m:kromka", mid=985)})
    assert f"w:c:kromka:{cz}" in kb_data(last("editMessageText")), kb_data(last("editMessageText"))
    jk2 = P.add_wait(rwx, "job", 0, "кромка", machine="kromka", code=cz)
    _cr = P.crew_for(rwx)
    A.db().execute("UPDATE raspil_crew SET helper_id=? WHERE id=?", (rwx["kw_id"] + 1000, _cr["id"])); A.db().commit()
    _jp = P.add_wait(rwx, "job", 0, "", machine="pris")
    _j2 = A.q("SELECT * FROM raspil_job WHERE id=?", (_jp,), one=True)
    assert _cr["machine"] != "pris" and _j2["op_id"] == rwx["kw_id"] and not _j2["helper_id"], dict(_j2)   # бошқа станок — ёзганнинг ўзига
    _jk = A.q("SELECT * FROM raspil_job WHERE id=?", (P.add_wait(rwx, "job", 0, "", machine="kromka"),), one=True)
    assert _jk["helper_id"] == rwx["kw_id"] + 1000                                       # ўз станоги — экипаж (оператор+ёрдамчи)
    A.db().execute("UPDATE raspil_crew SET helper_id=NULL WHERE id=?", (_cr["id"],))
    A.db().execute("UPDATE raspil_job SET status='cancelled' WHERE id IN (?,?)", (_jp, _jk["id"])); A.db().commit()
    assert P.wait_code(jk2) == cz and len(P.code_jobs(cz)) == 2
    lines_by["D9"] = [dict(L("Распил Кусок", 10, 11000, "кв.м"), no=1), dict(L("Кромка ПВХ Кашемир 19/1", 6, 4500, "пог.м"), no=2, uzs=27000)]
    for jj_ in (jd, jk2):
        ok, t = P.link_wait(jj_, "D9", "6399", "Мижоз"); assert ok, t
    assert A.q("SELECT sheets FROM raspil_job WHERE iddoc='D9' AND machine='dp' AND status='ok'", one=True)["sheets"] == 2
    assert A.q("SELECT pr_pcs FROM raspil_job WHERE iddoc='D9' AND machine='kromka' AND status='ok'", one=True)["pr_pcs"] == 6
    assert "тўланган" in P.code_status(cz)
    # двухпилка накладнойсиз: 1С «Распил ОБЫЧНЫЙ» хизматидан танлайди (эркин матн эмас)
    _rs = P.raspil_services; P.raspil_services = lambda m="dp": [("R45", "Распил под 45*", "м"), ("RK", "Распил Кусок", "м²"), ("RL", "Распил ЛДСП/ЛМДФ 5,796 кв", "та")]
    B.handle_update({"callback_query": cb("w:m:dp", mid=980)}); B.handle_update({"callback_query": cb("w:c:dp:0", mid=980)}); B.handle_update({"message": msg(mid=3980, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); d = kb_data(e); assert "Двухпилка" in e["text"] and "под 45*" in str(e), e
    B.handle_update({"callback_query": cb([x for x in d if x.endswith(":R45")][0], mid=980)})
    assert "Неча метр" in last("editMessageText")["text"]
    assert P.parse_area("200-500") == (0.1, "200×500") and P.parse_area("2800 400 2")[0] == 2.24
    assert P.parse_area("200x500\n300 400") [0] == 0.22 and P.parse_area("2,8х0,4")[0] == 1.12 and P.parse_area("1.5") == (1.5, "")
    B.handle_update({"message": msg(mid=981, text="3,5")})
    m5 = last(); assert "Распил под 45* — 3.5 м" in m5["text"], m5["text"]
    B.handle_update({"callback_query": cb([x for x in kb_data(m5) if x.startswith("rm:")][0], mid=982)})
    B.handle_update({"callback_query": cb([x for x in kb_data(last("editMessageText")) if x.endswith(":RL")][0], mid=982)})
    assert "Неча та" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=983, text="2")})
    assert "ЛДСП/ЛМДФ 5,796 кв — 2 та" in last()["text"], last()["text"]
    B.handle_update({"callback_query": cb([x for x in kb_data(last()) if x.startswith("rm:")][0], mid=984)})
    B.handle_update({"callback_query": cb([x for x in kb_data(last("editMessageText")) if x.endswith(":RK")][0], mid=984)})
    assert "ўлчами" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=985, text="200-500")})
    assert "Распил Кусок 200×500 — 0.1 м²" in last()["text"], last()["text"]
    jdp = A.q("SELECT * FROM raspil_job WHERE machine='dp' AND status='wait' ORDER BY id DESC LIMIT 1", one=True)
    assert "под 45* — 3.5 м" in jdp["text"], jdp["text"]
    P.raspil_services = _rs
    # менежер: рақами бор (роли 'ready'), mgr=1 → ботга уланади, Ц-код карточкаси келади ва янгиланади
    A.db().execute("INSERT INTO ready_workers(name, phone, created_at, role, mgr) VALUES('Жасур Бошкарувчи','998990778228',?, 'ready', 1)", (A.now(),)); A.db().commit()
    B.handle_update({"message": dict(msg(text=""), **{"from": {"id": 5551}, "chat": {"id": 5551, "type": "private"},
                                                     "contact": {"phone_number": "+998990778228", "user_id": 5551}})})
    assert "менежер" in last()["text"], last()
    B.handle_update({"message": dict(msg(text="Ц1"), **{"from": {"id": 5551}, "chat": {"id": 5551, "type": "private"}})})
    assert last()["chat_id"] == 5551
    P.notify_mgr(cz)
    mm = [p for m, p in sent if m == "sendMessage" and p.get("chat_id") == 5551][-1]
    assert f"🔖 {P.clabel(cz)}" in mm["text"] and "Тўланди" in mm["text"], mm["text"]
    # ровер накладнойсиз: сумма + изоҳ
    B.handle_update({"callback_query": cb("w:m:rover", mid=970)}); B.handle_update({"callback_query": cb("w:c:rover:0", mid=970)}); B.handle_update({"message": msg(mid=3970, text="Тест Мижоз 901234567")})
    assert "суммаси" in last("editMessageText")["text"], last("editMessageText")
    B.handle_update({"message": msg(mid=971, text="2 та фасад")}); assert "Фақат сумма" in last()["text"]
    B.handle_update({"message": msg(mid=972, text="150 000")})
    assert "150 000 сўм" in last()["text"] and "Нима қилдингиз" in last()["text"], last()["text"]
    B.handle_update({"message": msg(mid=973, text="2 та фасад фрезеровка")})
    jr = A.q("SELECT * FROM raspil_job WHERE machine='rover' AND status='wait' ORDER BY id DESC LIMIT 1", one=True)
    assert "Ровер иши — 150000 сўм · 2 та фасад фрезеровка" == jr["text"], jr["text"]
    cc = P.code_card(jr["wcode"]); assert "• Ровер иши — 150000 сўм" in cc and "💬 2 та фасад фрезеровка" in cc, cc
    lines_by["RV1"] = [dict(L("Услуга Ровер", 1, 150000, "шт"), no=1, uzs=150000)]
    ok, t_ = P.link_wait(jr["id"], "RV1", "8001", "М"); assert ok, t_
    rr = A.q("SELECT * FROM raspil_job WHERE iddoc='RV1' AND machine='rover' AND status='ok'", one=True)
    assert rr and rr["pr_sum"] == 150000 and rr["text"] == "2 та фасад фрезеровка", rr and dict(rr)
    # присадка накладнойсиз: 1С «9 Присадка» хизматидан танлайди, сонини ёзади; ЧПУ — сумма
    _rs2 = P.raspil_services
    P.raspil_services = lambda m="dp": [("PE", "Услуга сверления Евростяжка (лицевая 5*7)", "та"), ("PZ", "Услуга сверления Паз (под подсветки)", "м"),
                                        ("PC", "Услуга фрезеровка  (обработка ЧПУ)", "сўм")] if m == "pris" else []
    B.handle_update({"callback_query": cb("w:m:pris", mid=960)}); B.handle_update({"callback_query": cb("w:c:pris:0", mid=960)}); B.handle_update({"message": msg(mid=3960, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); d = kb_data(e); assert "Присадка" in e["text"] and "Евростяжка" in str(e), e
    B.handle_update({"callback_query": cb([x for x in d if x.endswith(":PE")][0], mid=960)}); assert "Неча та" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=961, text="24")}); m6 = last(); assert "Евростяжка (лицевая 5*7) — 24 та" in m6["text"], m6["text"]
    B.handle_update({"callback_query": cb([x for x in kb_data(m6) if x.startswith("rm:")][0], mid=962)})
    B.handle_update({"callback_query": cb([x for x in kb_data(last("editMessageText")) if x.endswith(":PC")][0], mid=962)})
    assert "Суммаси" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=963, text="120 000")}); assert "(обработка ЧПУ) — 120000 сўм" in last()["text"], last()["text"]
    # утолщенка накладнойсиз: 1С «8 Утолщенка» (клей, пистолет) → метр
    P.raspil_services = lambda m="dp": [("UK", "Утолщенка клей", "м"), ("UP", "Утолщенка пистолет", "м")] if m == "utol" else _rs2(m)
    B.handle_update({"callback_query": cb("w:m:utol", mid=964)}); B.handle_update({"callback_query": cb("w:c:utol:0", mid=964)})
    B.handle_update({"message": msg(mid=3964, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); assert "Утолщенка — нима" in e["text"] and any(x.endswith(":UP") for x in kb_data(e)), e
    B.handle_update({"callback_query": cb([x for x in kb_data(e) if x.endswith(":UP")][0], mid=964)}); assert "Неча метр" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=965, text="6")}); assert "Утолщенка пистолет — 6 м" in last()["text"], last()
    P.raspil_services = _rs2
    # кусок сотиш (ҳар қандай ишчи, ҳар қандай тартибда) — ойликка кирмайди, лекин мижоз кодида
    _kt2 = P.kusok_types; P.kusok_types = lambda: [("KL", "ЛДСП кусок"), ("KM", "ЛМДФ кусок")]
    _gd2 = _c1.goods; _c1.goods = lambda: {"KL": dict(name="ЛДСП кусок"), "KM": dict(name="ЛМДФ кусок")}
    B.handle_update({"callback_query": cb("sw:wait", mid=986)})
    assert "w:m:kus" in kb_data(last("editMessageText"))
    B.handle_update({"callback_query": cb("w:m:kus", mid=986)}); B.handle_update({"callback_query": cb("w:c:kus:0", mid=986)}); B.handle_update({"message": msg(mid=3986, text="Тест Мижоз 901234567")})
    e = last("editMessageText"); d = kb_data(e); assert "Кусок" in e["text"] and any(x.startswith("ks:") and x.endswith(":KL") for x in d), e
    B.handle_update({"callback_query": cb([x for x in d if x.endswith(":KL")][0], mid=986)})
    assert "Ўлчами" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=987, text="2800х1000 кашемир")})
    m4 = last(); assert "Кимники" in m4["text"] and any(x.startswith("ko:") for x in kb_data(m4)), m4
    B.handle_update({"message": msg(mid=988, text="Алишер")})
    assert "4 рақами" in last()["text"]
    B.handle_update({"message": msg(mid=989, text="алишер +998 90 123 11 31")})
    m4 = last(); assert "ЛДСП кусок кашемир 2800×1000 · Алишер 1131 — 2.8 м²" in m4["text"], m4["text"]
    js = A.q("SELECT * FROM raspil_job WHERE machine='kus' ORDER BY id DESC LIMIT 1", one=True)
    assert js["status"] == "wait" and "2.8 м²" in js["text"]
    assert A.q("SELECT status, src FROM raspil_kusok WHERE sold_job=?", (js["id"],), one=True)["status"] == "res"
    # омборга кусок киритиш → кейин сотишда омбордан танланади
    B.handle_update({"callback_query": cb("sw:kin", mid=990)})
    d = kb_data(last("editMessageText")); assert "Омборга" in last("editMessageText")["text"]
    B.handle_update({"callback_query": cb([x for x in d if x.endswith(":KM")][0], mid=990)})
    B.handle_update({"message": msg(mid=991, text="2,8 0,4")})
    assert "Ранги" in last()["text"], last()
    B.handle_update({"message": msg(mid=9911, text="12")}); assert "Ранги" in last()["text"]
    B.handle_update({"message": msg(mid=9912, text="белый")}); assert "Кимники" in last()["text"], last()
    B.handle_update({"callback_query": cb([x for x in kb_data(last()) if x.startswith("ko:")][0], mid=992)})
    assert "Омборга қўшилди" in last("editMessageText")["text"] and "РайГаз" in last("editMessageText")["text"], last("editMessageText")
    st = P.kusok_stock("KM"); assert len(st) == 1 and st[0]["m2"] == 1.12 and st[0]["owner"] == "РайГаз", st
    B.handle_update({"callback_query": cb("w:m:kus", mid=993)}); B.handle_update({"callback_query": cb("w:c:kus:0", mid=993)}); B.handle_update({"message": msg(mid=3993, text="Тест Мижоз 901234567")})
    d = kb_data(last("editMessageText")); B.handle_update({"callback_query": cb([x for x in d if x.endswith(":KM")][0], mid=993)})
    d = kb_data(last("editMessageText")); assert f"kx:" in " ".join(d) and any(x.startswith("kw:") for x in d), d
    B.handle_update({"callback_query": cb([x for x in d if x.startswith("kx:")][0], mid=993)})
    assert "белый 2,8×0,4 · РайГаз" in last("editMessageText")["text"] or "РайГаз" in last("editMessageText")["text"], last("editMessageText")
    assert not P.kusok_stock("KM")
    jk = A.q("SELECT * FROM raspil_job WHERE machine='kus' ORDER BY id DESC LIMIT 1", one=True)
    P.kusok_unsell(jk["id"]); assert len(P.kusok_stock("KM")) == 1
    assert P.parse_owner("райгаз") == "РайГаз" and P.parse_owner("Жамшид 1234") == "Жамшид 1234" and P.parse_owner("1234") is None
    pg = P.kusok_page(A.now()[:7]); assert pg["stock"] and pg["res"]
    B.handle_update({"callback_query": cb([x for x in kb_data(m4) if x.startswith("kz:")][0], mid=989)})
    mm = [p for m, p in sent if m == "sendMessage" and p.get("chat_id") == 5551][-1]
    assert f"🔖 {P.clabel(P.wait_code(js['id']))}" in mm["text"] and "Алишер 1131" in mm["text"] and "⏳" in mm["text"], mm["text"]
    ok, t = P.link_wait(js["id"], "D9", "6399", "Мижоз"); assert ok, t
    assert A.q("SELECT status FROM raspil_kusok WHERE sold_job=?", (js["id"],), one=True)["status"] == "sold"
    me = [p for m, p in sent if m == "editMessageText" and p.get("chat_id") == 5551][-1]
    assert "✅ Тўланди — №6399" in me["text"] and me["message_id"] == mm.get("message_id", me["message_id"]), me
    d = P.parse_kus("400 1500 10"); assert d["q"] == 10 and d["m"] == 0.6 and d["c"] == "", d
    # сони билан омборга: 3 та алоҳида кусок
    B.handle_update({"callback_query": cb("sw:kin", mid=995)})
    B.handle_update({"callback_query": cb([x for x in kb_data(last("editMessageText")) if x.endswith(":KM")][0], mid=995)})
    B.handle_update({"message": msg(mid=996, text="400 1500 3")}); assert "× 3 та" in last()["text"] and "Ранги" in last()["text"], last()
    B.handle_update({"message": msg(mid=997, text="оқ")})
    B.handle_update({"callback_query": cb([x for x in kb_data(last()) if x.startswith("ko:")][0], mid=998)})
    assert "× 3 та — 1.8 м²" in last("editMessageText")["text"], last("editMessageText")
    assert len([k for k in P.kusok_stock("KM") if k["color"] == "оқ"]) == 3
    # кусок омбори: рўйхат → босса сотишга (янги мижоз)
    assert B.B_KUS in str(B.MAIN_KB) and "sw:kst" not in str(B.switch_kb())
    B.handle_update({"message": msg(mid=9990, text=B.B_KUS)}); m_ = last(); assert "Кусок омбори" in m_["text"] and kb_data(m_)[:2] == ["sw:ksee", "sw:kin"] and "kb:" not in str(m_), m_
    B.handle_update({"callback_query": cb("sw:ksee", mid=999)})
    e = last("editMessageText"); assert "Омборда" in e["text"] and "🏭 РайГаз — 4 та" in str(e) and "kb:" not in str(e) and "sw:kst" in kb_data(e), e
    kg_ = [x for x in kb_data(e) if x.startswith("kg:")]; assert len(kg_) == 1, kg_
    B.handle_update({"callback_query": cb(kg_[0], mid=999)}); e = last("editMessageText")
    assert "ЛМДФ кусок оқ 400×1500 × 3 та — 1.8 м²" in str(e) and len([x for x in kb_data(e) if x.startswith("kb:")]) == 2, e
    kb3 = [b_["callback_data"] for r in e["reply_markup"]["inline_keyboard"] for b_ in r if "× 3 та" in b_["text"]][0]
    kid0 = int(kb3[3:]); n0 = len(P.kusok_stock())
    B.handle_update({"callback_query": cb(kb3, mid=999)}); e = last("editMessageText"); assert "Нечтаси" in e["text"] and f"kb:{kid0}:3" in kb_data(e), e
    B.handle_update({"callback_query": cb(f"kb:{kid0}:2", mid=999)}); e = last("editMessageText"); assert "Кимга?" in e["text"] and "× 2 та" in e["text"], e
    B.handle_update({"callback_query": cb(f"kc:{kid0}:0:2", mid=999)})
    B.handle_update({"message": msg(mid=1000, text="Ботир 4455")})
    e = last("editMessageText"); assert "Кусок" in e["text"] and "оқ 400×1500 · РайГаз × 2 та — 1.2 м²" in e["text"] and "kz:" in str(e), e
    assert len(P.kusok_stock()) == n0 - 2
    jb = A.q("SELECT * FROM raspil_job WHERE machine='kus' ORDER BY id DESC LIMIT 1", one=True); assert "Ботир 4455" in (P.code_client(jb["wcode"]) or ""), jb["wcode"]
    B.handle_update({"callback_query": cb(f"kb:{kid0}", mid=999)}); assert "сотилган" in last("editMessageText")["text"]
    # брон: эгаси ичида 🔒 × 2 → қайтариш (иккаласи) → яна омборда
    B.handle_update({"callback_query": cb(f"kg:{kid0}", mid=999)}); e = last("editMessageText")
    assert f"kr:{kid0}" in kb_data(e) and "🔒" in str(e) and "× 2" in str(e), e
    B.handle_update({"callback_query": cb(f"kr:{kid0}", mid=999)}); assert "kv:" in str(last("editMessageText"))
    B.handle_update({"callback_query": cb(f"kv:{kid0}", mid=999)}); assert "Нечтасини" in last("editMessageText")["text"] and f"kv:{kid0}:2" in kb_data(last("editMessageText"))
    B.handle_update({"callback_query": cb(f"kv:{kid0}:1", mid=999)}); assert "Омборга қайтарилди" in last("editMessageText")["text"]
    assert len(P.kusok_stock()) == n0 - 1 and A.q("SELECT status FROM raspil_job WHERE id=?", (jb["id"],), one=True)["status"] == "wait"
    assert len(P.json.loads(A.q("SELECT items FROM raspil_job WHERE id=?", (jb["id"],), one=True)["items"])["wait"]["pick"]) == 1
    rk = A.q("SELECT id FROM raspil_kusok WHERE sold_job=? AND status='res'", (jb["id"],), one=True)["id"]
    B.handle_update({"callback_query": cb(f"kv:{rk}", mid=999)}); assert "Омборга қайтарилди" in last("editMessageText")["text"]
    assert len(P.kusok_stock()) == n0
    assert A.q("SELECT status FROM raspil_job WHERE id=?", (jb["id"],), one=True)["status"] == "cancelled"
    # накладнойга брон (крой): 3 тадан 1 таси
    B.handle_update({"callback_query": cb(f"kb:{kid0}:1", mid=999)}); assert f"kn:{kid0}:1" in kb_data(last("editMessageText"))
    B.handle_update({"callback_query": cb(f"kn:{kid0}:1", mid=999)}); assert "Накладной рақами" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=1001, text="абв")}); assert "Фақат накладной" in last()["text"]
    B.handle_update({"message": msg(mid=1002, text="№ 6262")}); assert "№6262га брон" in last()["text"], last()
    assert P.kusok_sell(kid0, jb["id"]) is None                       # бронни бошқа сота олмайди
    B.handle_update({"callback_query": cb(f"kb:{kid0}", mid=999)}); assert "брон" in last("editMessageText")["text"]
    assert P.kusok_auto_sold({"X": dict(kind=210, posted=True, deleted=False, no="  6261")}) == 0
    assert P.kusok_auto_sold({"X": dict(kind=210, posted=True, deleted=False, no="000006262")}) == 1
    assert A.q("SELECT status FROM raspil_kusok WHERE id=?", (kid0,), one=True)["status"] == "sold"
    assert not P.kusok_return(kid0)[0]
    # менежер: омборни кўради, фақат накладнойга брон (Ц-код йўқ), қайтара олади
    B.handle_update({"message": dict(msg(text=B.B_KUS), **{"from": {"id": 5551}, "chat": {"id": 5551, "type": "private"}})})
    mk = last(); assert mk["chat_id"] == 5551 and "Кусок омбори" in mk["text"] and kb_data(mk) == ["sw:ksee"], mk
    mcb = lambda d: {"id": "q", "from": {"id": 5551}, "data": d, "message": {"chat": {"id": 5551}, "message_id": 77}}
    B.handle_update({"callback_query": mcb("sw:ksee")}); mk = last("editMessageText")
    B.handle_update({"callback_query": mcb([x for x in kb_data(mk) if x.startswith("kg:")][0])}); mk = last("editMessageText")
    kin = [x for x in kb_data(mk) if x.startswith("kb:")][-1]
    B.handle_update({"callback_query": mcb(kin + ":1")}); e = last("editMessageText"); assert "kn:" in str(e) and "kc:" not in str(e), e
    B.handle_update({"callback_query": mcb("kn:" + kin[3:] + ":1")})
    B.handle_update({"message": dict(msg(text="7001"), **{"from": {"id": 5551}, "chat": {"id": 5551, "type": "private"}})})
    assert "№7001га брон" in last()["text"], last()
    B.handle_update({"callback_query": mcb("kv:" + kin[3:])}); assert "қайтарилди" in last("editMessageText")["text"]
    # накладнойсиз → кусок сотиш: омбордагилар жамланган (× 2 та) → нечтаси
    B.handle_update({"callback_query": cb("w:m:kus", mid=1010)}); B.handle_update({"callback_query": cb("w:c:kus:0", mid=1010)})
    B.handle_update({"message": msg(mid=1011, text="Шерзод 7788")})
    d = kb_data(last("editMessageText")); B.handle_update({"callback_query": cb([x for x in d if x.endswith(":KM")][0], mid=1010)})
    e = last("editMessageText"); assert "оқ 400×1500 · РайГаз × 2 та — 1.2 м²" in str(e), e
    kx_ = [b_["callback_data"] for r in e["reply_markup"]["inline_keyboard"] for b_ in r if "× 2 та" in b_["text"]][0]
    B.handle_update({"callback_query": cb(kx_, mid=1010)}); e = last("editMessageText"); assert "Нечтаси" in e["text"], e
    ns = len(P.kusok_stock("KM")); B.handle_update({"callback_query": cb(kx_ + ":2", mid=1010)})
    assert "× 2 та — 1.2 м²" in last("editMessageText")["text"] and len(P.kusok_stock("KM")) == ns - 2, last("editMessageText")
    assert P.parse_size("2,8 1") [0] == 2.8 and P.parse_size("2800*400")[0] == 1.12
    P.kusok_types, _c1.goods = _kt2, _gd2
    # орада мижоз келди: кромка 50% да тўхтатилди → «📂 Очиқ ишлар» → давом
    lines_by["K2"] = [dict(L("Кромка ПВХ Кашемир 19/1", 20, 3000, "пог.м"), no=1, uzs=60000),
                      dict(L("Кромка ПВХ Белый 19/1", 10, 3000, "пог.м"), no=2, uzs=30000, note="кора")]
    docs["7003"] = "K2"
    B.handle_update({"message": msg(text=B.B_KROMKA)})
    B.handle_update({"callback_query": cb(f"op:kromka:{J}")}); B.handle_update({"callback_query": cb(f"hp:kromka:{J}:0")})
    B.handle_update({"message": msg(mid=60, text="7003")})
    p = last(); assert kb_data(p)[0].startswith("wl:") and "Кашемир 19/1 — 20 м" in json.dumps(p["reply_markup"], ensure_ascii=False), p
    assert "Белый 19/1 · 🎨 кора: 10 м" in p["text"] and "Белый 19/1 кора — 10 м" in json.dumps(p["reply_markup"], ensure_ascii=False), p
    B.handle_update({"callback_query": cb(kb_data(p)[0])})                                    # 1-кромка тугади
    e = last("editMessageText"); assert "✅ 1. Кромка ПВХ Кашемир" in e["text"] and "қолди 10 м" in e["text"], e["text"]
    B.handle_update({"callback_query": cb(f"j:{int(kb_data(p)[0].split(':')[1])}:close")})     # мижоз келди — ёпди
    B.handle_update({"message": msg(text=B.B_OPEN)})
    o = last(); ot = json.dumps(o["reply_markup"], ensure_ascii=False); assert "№7003" in ot and "10 м қолди" in ot and "7002" not in ot, ot
    B.handle_update({"callback_query": cb(kb_data(o)[0])})
    e = last("editMessageText"); assert "Давом" in e["text"] and "▫️ 2." in e["text"], e["text"]
    wm_ = [x for x in kb_data(e) if x.startswith("wm:")]; assert wm_, kb_data(e)                 # қисман: 4 м қилди
    B.handle_update({"callback_query": cb(wm_[0])}); assert "Неча метр қилдингиз" in last("editMessageText")["text"]
    B.handle_update({"message": msg(mid=61, text="абв")}); assert "Фақат рақам" in last()["text"]
    B.handle_update({"message": msg(mid=62, text="4")})
    e = last("editMessageText"); assert "4 м ёзилди" in e["text"] and "қилинди 4" in e["text"] and "қолди 6 м" in e["text"], e["text"]
    assert A.q("SELECT pr_pcs FROM raspil_job WHERE iddoc='K2' AND machine='kromka' ORDER BY id DESC LIMIT 1", one=True)["pr_pcs"] == 4
    B.handle_update({"callback_query": cb([x for x in kb_data(e) if x.startswith("wl:")][0])})
    assert "тўлиқ бажарилган" in last("editMessageText")["text"], last("editMessageText")["text"]
    assert A.q("SELECT pr_pcs FROM raspil_job WHERE iddoc='K2' AND machine='kromka' ORDER BY id DESC LIMIT 1", one=True)["pr_pcs"] == 6
    B.handle_update({"message": msg(text=B.B_OPEN)}); assert "Тугамаган иш йўқ" in last()["text"], last()["text"]
    # станок алмаштириш: Ж арра экипажидан кромкага ўтди → аррада шериги ёлғиз давом этади, Ж га арра иши ёзилмайди
    _now = P._now()
    P.start_crew_ids(rwx, "arra", 7101, 7102, at=_now - timedelta(minutes=30))
    P.start_crew_ids(rwx, "kromka", 7101, None, at=_now - timedelta(minutes=10))
    ar = [c for c in P.open_crews(kw=7102) if c["machine"] == "arra"]
    assert ar and ar[0]["op_id"] == 7102 and not ar[0]["helper_id"], ar
    assert not [c for c in P.open_crews(kw=7101) if c["machine"] == "arra"]
    P.start_crew_ids(rwx, "arra", 7101, None, at=_now - timedelta(minutes=1))          # қайтди
    assert [c for c in P.open_crews(kw=7101) if c["machine"] == "arra"] and not [c for c in P.open_crews(kw=7101) if c["machine"] == "kromka"]
    A.db().execute("UPDATE raspil_crew SET ended_at=? WHERE op_id IN (7101,7102) OR helper_id IN (7101,7102)", (P._fmt(_now),)); A.db().commit()
    P.sync_kpi(date.today().isoformat())
    q = {(r["kind"], r["shift"]): (r["qty"], r["rate"]) for r in A.q("SELECT c.kind, c.shift, k.qty, m.rate FROM k_kpi k JOIN k_kpi_cols c ON c.id=k.col_id "
                                                                    "JOIN k_kpi_mcols m ON m.col_id=c.id AND m.ym=k.ym WHERE c.worker_id=?", (J,))}
    assert q[("kromka", "d")][0] + q.get(("kromka", "n"), (0,))[0] == 101 and q[("rover", "d")] == (630000, 0.1), q
    # қўлда ўзгартирилган катак — автомат тегмайди
    rc = A.q("SELECT id FROM k_kpi_cols WHERE worker_id=? AND kind='rover'", (J,), one=True)["id"]
    ym_, d_ = date.today().isoformat()[:7], date.today().day
    A.db().execute("UPDATE k_kpi SET qty=111 WHERE ym=? AND day=? AND col_id=?", (ym_, d_, rc))
    A.db().execute("INSERT INTO k_kpi_lock(ym,day,col_id) VALUES(?,?,?)", (ym_, d_, rc)); A.db().commit()
    P.sync_kpi(date.today().isoformat())
    assert A.q("SELECT qty FROM k_kpi WHERE ym=? AND day=? AND col_id=?", (ym_, d_, rc), one=True)["qty"] == 111
    A.db().execute("DELETE FROM k_kpi_lock"); A.db().commit(); P.sync_kpi(date.today().isoformat())
    assert A.q("SELECT qty FROM k_kpi WHERE ym=? AND day=? AND col_id=?", (ym_, d_, rc), one=True)["qty"] == 630000
    B.handle_update({"message": msg(text=B.B_SAL)}); st_ = last()["text"]
    assert "Кромка: 101 м" in st_ and "Ровер: 6 шт" in st_, st_
    mt = P.machine_totals(date.today().isoformat())
    assert any(t["machine"] == "kromka" and t["wq"] == 101 for t in mt) and any(t["machine"] == "rover" and t["ws"] == 630000 for t in mt), mt
    # бошқа ишчи (распилсиз): фақат келди-кетди, табел ўзи
    A.db().execute("INSERT INTO k_workers(name, salary) VALUES('Дилмурод', 1200000)"); A.db().commit()
    D = A.q("SELECT id FROM k_workers WHERE name='Дилмурод'", one=True)["id"]
    A.db().execute("INSERT INTO ready_workers(name,phone,role,kw_id,active,last_id) VALUES('Дилмурод','998905550000','staff',?,1,1)", (D,)); A.db().commit()
    U2 = 777
    m2 = lambda **k: dict(dict(message_id=1, chat={"id": U2, "type": "private"}, **{"from": {"id": U2}}), **k)
    B.handle_update({"message": m2(contact={"phone_number": "998905550000", "user_id": U2})})
    assert "Дилмурод" in last()["text"] and last()["reply_markup"] == B._kb_add_lang(B._kb_add_pay(B.STAFF_KB))
    B.handle_update({"message": m2(text=B.B_IN)}); assert "ишга келдингиз" in last()["text"]
    B.handle_update({"message": m2(text=B.B_IN)}); assert "белгилангансиз" in last()["text"]
    import kassa as K
    ym = date.today().isoformat()[:7]
    ts = A.q("SELECT ts FROM k_ws WHERE ym=? AND worker_id=?", (ym, D), one=True)["ts"]
    assert K.ts_list(ts)[date.today().day - 1] == "9", ts
    B.handle_update({"message": m2(text=B.B_END)}); assert "иш тугади" in last()["text"]
    B.handle_update({"message": m2(photo=[{"file_id": "z"}])}); assert "Ишга келдим" in last()["text"]
    # шерик: фақат ҳисобот; ишчи-шерик: тугма қўшилади; 19:00 да кун якуни, кейин касса ўзгарса — хабар
    import owner as O
    from datetime import datetime
    A.db().execute("INSERT INTO ready_workers(name,phone,role,active,owner) VALUES('Лочин','998901112233','ready',1,1)")
    A.db().execute("UPDATE ready_workers SET owner=1 WHERE phone='998905550000'"); A.db().commit()
    U3 = 888
    m3 = lambda **k: dict(dict(message_id=1, chat={"id": U3, "type": "private"}, **{"from": {"id": U3}}), **k)
    B.handle_update({"message": m3(contact={"phone_number": "+998 90 111 22 33", "user_id": U3})})
    assert "Лочин" in last()["text"] and last()["reply_markup"] == B._kb_add_lang(B._kb_add_pay(B.OWN_KB)), last()
    B.handle_update({"message": m3(text=B.B_OWN)}); t_ = last()
    assert "💵 Касса" in t_["text"] and "🧾 Савдо" in t_["text"] and "🏭 Цех" in t_["text"] and "own:debt" in str(t_), t_
    cbo = lambda d: {"id": "q", "from": {"id": U3}, "data": d, "message": {"chat": {"id": U3}, "message_id": 5}}
    for k_ in ("kassa", "shop", "month", "debt", "fact", "yday"):
        B.handle_update({"callback_query": cbo("own:" + k_)}); assert "хато" not in last("editMessageText")["text"], (k_, last("editMessageText"))
    B.handle_update({"message": m2(text="/start")}); assert B.B_OWN in str(last()["reply_markup"]), last()
    B.handle_update({"message": m2(text=B.B_OWN)}); assert "💵 Касса" in last()["text"]
    n0 = len(sent); A.set_settings(owner_hour="19:00", own_sent="")
    O.tick(datetime.now().replace(hour=18, minute=59)); assert len(sent) == n0
    O.tick(datetime.now().replace(hour=19, minute=1))
    rep = [p_ for m_, p_ in sent[n0:] if m_ == "sendMessage"]; assert {p_["chat_id"] for p_ in rep} == {U2, U3} and "Кун якуни" in rep[0]["text"], rep
    O.tick(datetime.now().replace(hour=19, minute=2)); assert len(sent) == n0 + 2          # ўзгариш йўқ — хабар йўқ
    K.ensure_month(ym)
    A.db().execute("INSERT OR REPLACE INTO k_days(ym, day, rate, in1_uzs, fact_uzs, has_fact, updated_at) VALUES(?,?,12000,500000,500000,1,?)",
                   (ym, date.today().day, A.now())); A.db().commit()
    O.tick(datetime.now().replace(hour=20, minute=40))
    ch = [p_ for m_, p_ in sent[n0 + 2:] if m_ == "sendMessage"]
    assert len(ch) == 2 and "Касса ўзгартирилди" in ch[0]["text"] and "Ўзгарган вақти" in ch[0]["text"] and "Кирим" in ch[0]["text"], ch
    O.tick(datetime.now().replace(hour=20, minute=41)); assert len(sent) == n0 + 4
    # интернет секин: расм юкланмаса — навбатга, кейин ўзи юкланиб давом этади
    import requests as _rq
    def _bad(*a, **k): raise _rq.ConnectTimeout("timeout")
    B.requests.get = _bad
    B.handle_update({"message": msg(mid=901, photo=[{"file_id": "slow1"}], caption="6242")})
    assert "Интернет секин" in last()["text"]
    assert A.q("SELECT COUNT(*) FROM raspil_photo_retry WHERE state=''", one=True)[0] == 1
    B.retry_photos(force=True); assert A.q("SELECT tries FROM raspil_photo_retry WHERE file_id='slow1'", one=True)[0] == 1
    B.requests.get = lambda *a, **k: FakeResp()
    n1 = len(sent)
    B.retry_photos(force=True)
    assert A.q("SELECT state FROM raspil_photo_retry WHERE file_id='slow1'", one=True)[0] == "done"
    assert any(m_ == "sendMessage" and "6242" in (p_.get("text") or "") for m_, p_ in sent[n1:]), sent[n1:]
    # 15 марта юкланмаса — «қайта юборинг»
    B.requests.get = _bad
    B.handle_update({"message": msg(mid=902, photo=[{"file_id": "slow2"}], caption="6242")})
    for _ in range(B.PHOTO_RETRY_MAX):
        B.retry_photos(force=True)
    assert A.q("SELECT state FROM raspil_photo_retry WHERE file_id='slow2'", one=True)[0] == "fail" and "Қайта юборинг" in last()["text"]
    B.requests.get = lambda *a, **k: FakeResp()
print("RASPIL BOT OK")
