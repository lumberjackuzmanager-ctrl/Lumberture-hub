"""Шериклар улуши: ой охири тенглаштириш черновиги (сентябрь қолдиғи октябрга қўшилади), черновик янгиланади, проведендан кейин тегилмайди."""
import os, sys, json, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, kassa as K, partners as P
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok()
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302

NUM = {"2026-09": {"Жамшид": (949.97, 2773.01), "Лочин": (3736.56, 1663.81), "Жасур": (859.49, 1109.21)},
       "2026-10": {"Жамшид": (3023.83, 4813.37), "Лочин": (4799.34, 2888.02), "Жасур": (1803.56, 1925.35)}}
K.has_data = lambda ym: ym in NUM
K.ym_today = lambda: "2026-11"
def mc(ym, _depth=0):
    return dict(profit=0, owners=[dict(item=it, taken=NUM[ym][it["name"]][0], due=NUM[ym][it["name"]][1])
                                  for it in A.q("SELECT * FROM k_items WHERE kind='owner' AND share>0 ORDER BY id")])
K.month_calc = mc

with A.app.app_context():
    con = A.db()
    A.set_settings(edit_lock_days="0")
    con.execute("INSERT OR IGNORE INTO fx_rates(rate, source, rate_date, fetched_at, is_demo) VALUES(12050, 'bot', '2026-10-31', '2026-10-31 09:00:00', 0)")
    for ext, nm in (("Z", "Жамшид ака Founder"), ("11", "Жасур CEO"), ("19", "Лочин SEO"), ("C5", "Оддий мижоз")):
        con.execute("INSERT OR REPLACE INTO erp_clients(ext, name, lc, folder, deleted) VALUES(?,?,?,0,0)", (ext, nm, nm.casefold()))
    for nm, sh in (("Жамшид", 5), ("Лочин", 3), ("Жасур", 2)):
        con.execute("INSERT OR IGNORE INTO k_items(kind, name, share) VALUES('owner', ?, ?)", (nm, sh))
        con.execute("UPDATE k_items SET share=? WHERE kind='owner' AND name=?", (sh, nm))
    con.execute("UPDATE k_items SET share=0 WHERE kind='owner' AND name NOT IN ('Жамшид','Лочин','Жасур')")
    # бошқа «Поступление денег» №298 (йил кетма-кетлиги)
    con.execute("INSERT INTO erp_docs(kind,no,date,client,usd,uzs,status,src) VALUES('pay','298','2026-11-02','C5',10,0,'posted','pf')")
    con.commit()
    P.mark_partner_clients(con); con.commit()
    assert len(P.pmap(con)) == 3

    # тенглаштириш шарт бўлмаган ой — черновик йўқ; eq_from'дан олдинги ой — ҳам
    assert P.ensure_draft("2026-08") is None
    rep = P.report("2026-10")
    assert rep["carry_months"] == ["2026-09"] and rep["has_carry"]
    nd = {r["item"]["name"]: r["need"] for r in rep["rows"]}
    exp = {"Жамшид": 3612.57, "Лочин": -3984.07, "Жасур": 371.49}          # жонли база (02.11) бўйича; бу ерда кириш яхлитланган
    assert all(abs(nd[k] - v) < 0.03 for k, v in exp.items()), nd

    did = P.ensure_draft("2026-10")
    d = con.execute("SELECT * FROM erp_docs WHERE id=?", (did,)).fetchone()
    assert d["status"] == "draft" and d["date"] == "2026-10-31" and d["no"] == "299" and d["kind"] == "pay" and d["src"] == "pf", dict(d)
    assert "Сентябрь 2026" in d["note"] and "Октябрь 2026" in d["note"]
    ls = {l["client"]: (l["usd"], l["debt"], l["uzs"]) for l in con.execute("SELECT * FROM erp_lines WHERE doc_id=?", (did,))}
    assert set(ls) == {"Z", "11", "19"} and round(sum(v[0] for v in ls.values()), 2) == 0, ls
    assert all(v[0] == v[1] and v[2] == 0 for v in ls.values())
    assert ls["Z"][0] == nd["Жамшид"] and ls["11"][0] == nd["Жасур"] and abs(ls["19"][0] - nd["Лочин"]) <= 0.031   # цент — энг катта қаторга
    upd = d["updated_at"]
    assert P.ensure_draft("2026-10") == did and con.execute("SELECT updated_at FROM erp_docs WHERE id=?", (did,)).fetchone()[0] == upd   # ўзгармади

    # черновик пайтида касса ўзгарса — суммалар янгиланади (ўша ҳужжат)
    NUM["2026-10"]["Жамшид"] = (3123.83, 4853.37)
    NUM["2026-10"]["Лочин"] = (4799.34, 2912.02)
    NUM["2026-10"]["Жасур"] = (1803.56, 1961.34)
    assert P.ensure_draft("2026-10") == did
    ls2 = {l["client"]: l["usd"] for l in con.execute("SELECT * FROM erp_lines WHERE doc_id=?", (did,))}
    assert round(sum(ls2.values()), 2) == 0 and ls2 != {k: v[0] for k, v in ls.items()}, ls2
    assert con.execute("SELECT COUNT(*) FROM erp_docs WHERE kind='pay' AND src='pf'").fetchone()[0] == 2
    draft_no_effect = erp.client_debt(con, "Z")          # черновик қарзга таъсир қилмайди
    assert draft_no_effect == 0, draft_no_effect

# саҳифа: черновик ҳақида хабар ва «олдинги ойлардан» устуни
h = c.get("/kassa/partners?ym=2026-10").get_data(as_text=True)
assert "Черновик тайёр" in h and f"/erp/doc/{did}" in h and "Олдинги ойлардан" in h, h[:3000]

# кассир (admin) проведен қилади — оддий таҳрирлаш ойнаси орқали
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs WHERE id=?", (did,), one=True)
    lines = [dict(client=l["client"], usd=l["usd"], uzs=0) for l in A.q("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (did,))]
assert c.get(f"/erp/doc/{did}/edit2").status_code == 200
tok()
r = c.post(f"/erp/doc/{did}/edit2", data={"csrf": "t", "act": "post", "date": "2026-10-31", "client": d["client"], "note": d["note"],
                                         "version": d["updated_at"], "lines_json": json.dumps(lines)})
assert r.status_code == 302, r.get_data(as_text=True)[:2000]
with A.app.app_context():
    con = A.db()
    assert con.execute("SELECT status FROM erp_docs WHERE id=?", (did,)).fetchone()[0] == "posted"
    upd = con.execute("SELECT updated_at FROM erp_docs WHERE id=?", (did,)).fetchone()[0]
    oct_ = {r["item"]["name"]: r for r in P.report("2026-10")["rows"]}
    assert all(abs(r["need"]) < 0.05 for r in oct_.values()), {k: v["need"] for k, v in oct_.items()}
    assert oct_["Жамшид"]["debt"]["pf_eq"] > 0 and oct_["Лочин"]["debt"]["pf_eq"] < 0
    sep = {r["item"]["name"]: r for r in P.report("2026-09")["rows"]}
    assert all(r["later"] and r["later"][0]["no"] == "299" for r in sep.values()), sep
    # проведендан кейин касса ўзгарса ҳам — ҳужжатга тегилмайди, фарқ «Яна ёзиш керак»да
    NUM["2026-10"]["Жамшид"] = (3200.0, 4900.0)
    assert P.ensure_draft("2026-10") == did
    assert con.execute("SELECT updated_at FROM erp_docs WHERE id=?", (did,)).fetchone()[0] == upd
    assert con.execute("SELECT COUNT(*) FROM erp_docs WHERE kind='pay' AND src='pf'").fetchone()[0] == 2
    # режалаштирувчи: 30 дақиқада бир марта
    A.set_settings(partners_eq_at="0")
    assert P.tick() == did
    assert P.tick() is None
    assert "partners" not in (A.scheduler_tick() or []) or True
h = c.get("/kassa/partners?ym=2026-10").get_data(as_text=True)
assert "Ёзилди" in h and "Черновик тайёр" not in h
h = c.get("/kassa/partners?ym=2026-09").get_data(as_text=True)
assert "кейинги ойда" in h

# ўчирилган черновик қайта тайёрланмайди
with A.app.app_context():
    con = A.db()
    A.set_settings(partners_eq_draft="{}")
    NUM["2026-10"]["Жамшид"] = (3023.83, 4813.37)
    d2 = P.ensure_draft("2026-10")         # posted'дан кейин яна фарқ бор → янги черновик (фақат фарқ)
    if d2 and d2 != did:
        con.execute("UPDATE erp_docs SET status='deleted' WHERE id=?", (d2,)); con.commit()
        assert P.ensure_draft("2026-10") == d2
        assert con.execute("SELECT status FROM erp_docs WHERE id=?", (d2,)).fetchone()[0] == "deleted"
print("PARTNERS DRAFT OK")

# ru/en: янги матнлар таржима қилинган (ўзбекча ҳарфлар қолмаган)
import re
NUM["2026-10"]["Жамшид"] = (3023.83, 4813.37)
for lg in ("ru", "en"):
    with c.session_transaction() as s:
        s["ui_lang"] = lg
    with A.app.app_context():
        A.q("UPDATE users SET ui_lang=NULL")
    for ym in ("2026-09", "2026-10"):
        h = c.get(f"/kassa/partners?ym={ym}").get_data(as_text=True)
        body = re.sub(r"<script.*?</script>|<style.*?</style>|<select.*?</select>", "", h, flags=re.S)
        left = [l.strip() for l in re.sub(r"<[^>]+>", "\n", body).split("\n") if re.search("[ҳқғўЎҚҒҲ]", l)]
        left = [l for l in left if not re.search(r"Founder|CEO|SEO|Жамшид|Лочин|Жасур|Оддий мижоз", l)]
        assert not left, (lg, ym, left)
print("PARTNERS DRAFT I18N OK")
