"""Шериклар: улуш ҳисоби (касса) ва қарз ҳисоби (1С). RENT_C1_TEST=1С нусхаси."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("PARTNERS TEST SKIPPED"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, partners as P
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok()
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
tok(); assert c.post("/1c/settings", data={"c1_path": p, "csrf": "t"}).status_code == 302
with A.app.app_context():
    erp.sync(force=True)
    con = A.db()
    for nm, sh in (("Жамшид", 5), ("Жасур", 2), ("Лочин", 3)):
        con.execute("INSERT OR IGNORE INTO k_items(kind, name, share) VALUES('owner', ?, ?)", (nm, sh))
        con.execute("UPDATE k_items SET share=? WHERE kind='owner' AND name=?", (sh, nm))
    con.commit()
    m = P.pmap(con)
    names = {A.q("SELECT name FROM k_items WHERE id=?", (k,), one=True)[0]: A.q("SELECT name FROM erp_clients WHERE ext=?", (v,), one=True)[0] for k, v in m.items()}
    assert names.get("Жамшид") == "Жамшид ака Founder" and names.get("Жасур") == "Жасур CEO" and names.get("Лочин") == "Лочин SEO", names
    P.mark_partner_clients(con); con.commit()
    for ext in m.values():
        ci = erp.credit_info(con, ext)
        assert ci["kind"] == "partner" and ci["nolimit"] and not ci["blocked"]
    eq = P.equalization_docs(con, set(m.values()))
    assert any(abs(sum(v.values())) < 0.5 and len(v) == 3 for v in eq.values()), eq
    for ym in ("2026-08", "2026-09"):
        rep = P.report(ym)
        assert len(rep["rows"]) == 3
        for r in rep["rows"]:
            d = r["debt"]
            calc = d["open"] + d["goods"] - d["inv_paid"] + d["taken"] - d["paid"] - d["eq"] + d["other"]
            assert abs(calc - d["close"]) < 0.05, (ym, r["item"]["name"], d, calc)
    aug = {r["item"]["name"]: r for r in P.report("2026-08")["rows"]}
    assert round(sum(r["posted"] for r in aug.values()), 2) == 0 and aug["Лочин"]["posted"] < 0      # 31.08 тенглаштириш
h = c.get("/kassa/partners?ym=2026-08").get_data(as_text=True)
assert "Улуш ҳисоби" in h and "Қарз ҳисоби" in h and "Жамшид ака Founder" in h
with A.app.app_context():
    z = A.q("SELECT id FROM k_items WHERE kind='owner' AND name='Жамшид'", one=True)[0]
tok(); assert c.post("/kassa/partners", data={"csrf": "t", "ym": "2026-08", f"c_{z}": m[z]}).status_code == 302
print("PARTNERS OK")
