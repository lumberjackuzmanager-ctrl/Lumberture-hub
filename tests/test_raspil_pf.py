"""1С ўчгач (26.10.2026): платформада проведен накладной (каталог товарлари) — цех боти, KPI, кромка/овал/ровер/присадка, листлар,
бот рўйхатлари (каталогдан), Базис присадка, кройчи, накладнойсиз иш автомат уланиши (телефон бўйича)."""
import os, sys, tempfile, json, re, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, raspil as P, catalog as C, c1, tg

tg.call = lambda token, method, params=None, timeout=15: {"message_id": 1} if method == "sendMessage" else True
c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}
c1.goods = lambda: {}
erp.stock_now = lambda: {}
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
today = datetime.date.today().isoformat()
ym = today[:7]

with A.app.app_context():
    con = A.db()
    A.set_settings(erp_test_mode="0", cat_switch=today, credit_init="1")
    nodes = [(1, 0, "Услуги", None, 1), (2, 1, "Распил", None, 1), (3, 2, "Распил ОБЫЧНЫЙ", None, 1), (4, 1, "Кромка", None, 1),
             (5, 4, "Кромка Эггер", None, 1), (6, 1, "Кромка Овал", None, 1), (7, 6, "Кромка Эггер", None, 1), (8, 1, "Ровер", None, 1),
             (9, 1, "Присадка", None, 1), (10, 0, "ЛДСП", 1, 0), (11, 10, "Эггер ЛДСП", None, 0), (12, 0, "Столешницы", 1, 0),
             (13, 0, "ХДФ/ДВП", 1, 0), (14, 1, "Утолшенка", None, 1), (15, 0, "Остатки (кусок)", 1, 0)]
    for nid, par, nm, wh, svc in nodes:
        con.execute("INSERT INTO cat_nodes(id,parent,name,wh,svc,skey,deleted,manual) VALUES(?,?,?,?,?,?,0,1)", (nid, par, nm, wh, svc, f"x:t{nid}"))
    items = [(101, 3, "Распил ЛДСП/ЛМДФ 5,796 кв", "кол-в", 1, 40000, 1), (102, 3, "Распил ХДФ", "кол-в", 1, 20000, 1),
             (103, 5, "Услуга ПВХ Эггер 19/0,8 Бежевый ПВХ ST9", "пог.м", 1, 9400, 1), (104, 7, "Услуга Овал Эггер 19/0,8 Бежевый ПВХ ST9", "пог.м", 1, 12100, 1),
             (105, 8, "Услуга Ровер", "шт", 1, 100000, 1), (106, 9, "Услуга сверления Под петли (35мм)", "шт", 1, 2000, 1),
             (107, 11, "ЛДСП Эггер Белый 16мм W1000", "шт", 2, 60, 0), (108, 12, "Столешница Дуб 38мм", "м2", 2, 50, 0),
             (109, 13, "ХДФ белый 3мм", "лист", 2, 8, 0), (110, 14, "Услуга обманка (клей)", "пог.м", 1, 5000, 1),
             (120, 15, "ЛМДФ кусок", "кв.м", 2, 20, 0)]
    for iid, node, nm, unit, cur, price, svc in items:
        con.execute("INSERT INTO cat_items(id,node,name,lc,unit,cur,price,svc,kind,skey,deleted,manual,src_manual) VALUES(?,?,?,?,?,?,?,?,?,?,0,1,1)",
                    (iid, node, nm, C.lc(nm), unit, cur, price, svc, "svc" if svc else "good", "c1g:K1" if iid == 120 else f"x:i{iid}"))
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (today, today + " 09:00:00"))
    C.mirror(con)
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c) "
                "VALUES('C1','C1','Азиз мебель','','+998 90 555 44 33','0',0,0,'азиз мебель','1c',0)")
    con.execute("UPDATE erp_clients SET blocked=0, lim=100000 WHERE ext='C1'")
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Улмас','1',?, 'raspil', 701, 1)", (A.now(),))
    con.commit()
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=701").fetchone())
    assert con.execute("SELECT unit FROM erp_goods WHERE ext='ci107'").fetchone()[0] == "шт"

lines = [("ci107", "3"), ("ci108", "1.5"), ("ci109", "4"), ("ci101", "3"), ("ci102", "2"), ("ci103", "12"), ("ci104", "5"),
         ("ci105", "1"), ("ci106", "10"), ("ci110", "2")]
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000",
                                         "good": [g for g, _ in lines], "qty": [q for _, q in lines],
                                         "price": ["60", "50", "8", "40000", "20000", "9400", "12100", "100000", "2000", "5000"],
                                         "lnote": [""] * len(lines), "kroychi": "", "konstr": ""})
assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    con = A.db()
    d = A.q("SELECT * FROM erp_docs WHERE kind='sale' AND src='pf' ORDER BY id DESC LIMIT 1", one=True)
    assert d and d["status"] == "posted", dict(d) if d else None
    iddoc, no = f"pf:{d['id']}", str(d["no"])

    # платформа плитаси (каталогда «шт») — лист; столешница ва кромка хизмати — лист эмас
    ls = P.sale_lines(iddoc)
    units = {l["gid"]: l["unit"] for l in ls}
    assert units["ci107"] == "лист" and units["ci108"] == "м2" and units["ci109"] == "лист", units
    items, caps, cheap = P.sheet_items(ls)
    kinds = {i["label"]: (i["kind"], i["qty"]) for i in items}
    assert kinds.get("ЛДСП Эггер Белый 16мм W1000") == ("main", 3.0) and kinds.get("ХДФ белый 3мм") == ("hdf", 4.0), kinds
    assert caps["main"] == 3 and caps["hdf"] == 2, caps
    tot, m45, det = P.calc(ls)
    assert tot == 4.0, (tot, det)                                   # 3 ЛДСП + ХДФ 2÷2

    # кромка / овал / ровер / утолщенка / присадка — каталог папкаси бўйича
    assert [w[1] for w in P.work_lines("kromka", ls)] == ["Услуга ПВХ Эггер 19/0,8 Бежевый ПВХ ST9"]
    assert [w[1] for w in P.work_lines("oval", ls)] == ["Услуга Овал Эггер 19/0,8 Бежевый ПВХ ST9"]
    assert [w[1] for w in P.work_lines("rover", ls)] == ["Услуга Ровер"]
    assert [w[1] for w in P.work_lines("utol", ls)] == ["Услуга обманка (клей)"]
    pcs, psum, _det = P.job_calc("pris", ls)
    assert pcs == 10, pcs

    # ишчи кромкани тугатди — KPI га тушади
    jid, err = P.record_work_lines(rw, "kromka", iddoc, no, "Азиз мебель")
    assert jid and not err, err
    j = A.q("SELECT * FROM raspil_job WHERE id=?", (jid,), one=True)
    assert j["status"] == "ok" and j["pr_pcs"] == 12, dict(j)
    jid2, err = P.record_work_lines(rw, "kromka", iddoc, no, "Азиз мебель")
    assert not jid2 and err, err                                    # иккинчи марта — йўқ
    st = P.items_state(iddoc)
    assert {i["label"]: i["left"] for i in st["items"]}["ЛДСП Эггер Белый 16мм W1000"] == 3.0, st["items"]
    ds = P.doc_state(iddoc)
    assert ds["tot"] == 4.0 and ds["left"]["main"] == 3.0, ds

    # бот рўйхатлари — каталогдан (cat_switch санасидан)
    assert P.cat_mode()
    tree, roots = P.kromka_tree("kromka")
    its = [nm for n in tree.values() for _k, nm in n["items"]]
    assert its == ["Услуга ПВХ Эггер 19/0,8 Бежевый ПВХ ST9"], its
    tree, roots = P.kromka_tree("oval")
    assert [nm for n in tree.values() for _k, nm in n["items"]] == ["Услуга Овал Эггер 19/0,8 Бежевый ПВХ ST9"]
    assert [x[1] for x in P.raspil_services("dp")] == ["Распил ЛДСП/ЛМДФ 5,796 кв", "Распил ХДФ"] or \
        sorted(x[1] for x in P.raspil_services("dp")) == ["Распил ЛДСП/ЛМДФ 5,796 кв", "Распил ХДФ"]
    assert [x[1] for x in P.raspil_services("pris")] == ["Услуга сверления Под петли (35мм)"]
    assert [x[1] for x in P.raspil_services("utol")] == ["Услуга обманка (клей)"]
    assert P.kromka_search("бежевый")[0][0] == "ci103"

    # Базис присадка: платформа накладнойидаги присадка суммаси ҳам
    des, pris = P.basis_month(ym)
    assert round(pris[int(today[8:10])]) == 20000, dict(pris)

    # кройчи: платформа плитаси «шт» бўлса ҳам лист ҳисобланади
    con.execute("UPDATE erp_docs SET kroychi=? WHERE id=?", (5, d["id"])); con.commit()
    kr = P.pf_kroy_docs(ym)
    assert kr and kr[0]["main"] == 3.0 and kr[0]["hdf"] == 2.0, kr          # распил сонидан (лист сонидан эмас, эгаси 25.10)

    # накладнойсиз иш (телефон ёзилган) → платформа накладнойига автомат уланади
    meta = P._pf_sales_meta(datetime.date.today() - datetime.timedelta(days=2))
    assert iddoc in meta[0] and meta[3][meta[2][iddoc]]["phones"] == "+998 90 555 44 33"
    wj = P.add_wait(rw, "main", 2, note="Азиз 905554433")
    A.db().execute("UPDATE raspil_job SET text='Азиз 905554433', client='Азиз 905554433' WHERE id=?", (wj,)); A.db().commit()
    n = P.auto_link_waits()
    w = A.q("SELECT status, iddoc FROM raspil_job WHERE id=?", (wj,), one=True)
    assert w["status"] == "linked" and w["iddoc"] == iddoc, (n, dict(w))
    done = A.q("SELECT COALESCE(SUM(sh_main),0) FROM raspil_job WHERE iddoc=? AND status='ok'", (iddoc,), one=True)[0]
    assert done == 2.0, done
print("RASPIL PF OK")

# цех ботида кусок сотилди (1С кусок тури) → черновикда каталог товари; проведен бўлганда бошқа кусок қайта сотилмайди
with A.app.app_context():
    con = A.db()
    k1 = P.kusok_add(rw, "K1", "ЛМДФ кусок", "Кашемир", "1000×500", 0.5, P.OWNER_CO)
    k2 = P.kusok_add(rw, "K1", "ЛМДФ кусок", "Белый", "1000×500", 0.5, P.OWNER_CO)
    code = P.new_code("Азиз")
    jk = P.add_wait(rw, "sale", 0, machine=P.SALE, code=code)
    assert P.kusok_sell(k1, jk, rw)
    did = P.code_row(code)["doc_id"]
    assert [tuple(x) for x in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (did,))] == [("ci120", 0.5)]
    con.execute("UPDATE erp_docs SET client='C1' WHERE id=?", (did,)); con.commit()
tok(); r = c.post(f"/erp/doc/{did}/status", data=dict(csrf="t", act="post"))
with A.app.app_context():
    assert A.q("SELECT status FROM erp_docs WHERE id=?", (did,), one=True)[0] == "posted"
    P.pf_link_waits()
    st = {k["id"]: k["status"] for k in A.q("SELECT id, status FROM raspil_kusok")}
    assert st[k1] == "sold" and st[k2] == "in", st
print("RASPIL PF KUSOK OK")
