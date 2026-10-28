"""28.10: касса саҳифаси очилгандан кейин накладной проведен бўлди (№6622) — кассир «Кирим: реализация»га тегмай сақлади,
эски сумма «қўлда ўзгартирилган» бўлиб қолиб, қизил огоҳлантириш чиқди. Энди тегилмаган сумма — ҳужжатлардаги янгиси."""
import os, sys, tempfile
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, kassa as K

T = date.today(); YM = T.strftime("%Y-%m")
K.onec_day = lambda d: dict(usd=47.34, uzs=600000.0, n=5, s_uzs=3620000.0, s_usd=300.0, s_n=10, pf=15)   # ҳозирги ҳужжатлар
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with c.session_transaction() as s: s["csrf"] = "t"
base = {"ym": YM, "d": str(T.day), "rate": "11950", "csrf": "t", "in1_uzs": "600000", "in1_usd": "47.34", "in1_auto_uzs": "600000", "in1_auto_usd": "47.34"}
# саҳифа очилганда ҳужжатларда 3 615 000 эди — кассир тегмади
c.post("/kassa/day", data=dict(base, in2_uzs="3615000", in2_usd="300", in2_auto_uzs="3615000", in2_auto_usd="300"))
with A.app.app_context():
    r = A.q("SELECT * FROM k_days WHERE ym=? AND day=?", (YM, T.day), one=True)
    assert r["in2_uzs"] == 3620000 and r["in2_man"] == 0 and r["in1_man"] == 0, dict(r)
# кассир ўзи ўзгартирди — қўлда (огоҳлантириш тўғри)
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/kassa/day", data=dict(base, in2_uzs="3500000", in2_usd="300", in2_auto_uzs="3620000", in2_auto_usd="300"))
with A.app.app_context():
    r = A.q("SELECT * FROM k_days WHERE ym=? AND day=?", (YM, T.day), one=True)
    assert r["in2_uzs"] == 3500000 and r["in2_man"] == 1, dict(r)
h = c.get(f"/kassa?ym={YM}&d={T.day}").get_data(as_text=True)
assert 'name="in2_auto_uzs" value="3620000' in h and "фарқ қилади (қўлда" in h
print("KASSA STALE OK")
