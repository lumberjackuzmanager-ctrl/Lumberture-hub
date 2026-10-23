"""Мижоз ҳисоботи (PDF) ва «hisobot» сўрови. RENT_C1_TEST керак."""
import os, sys, tempfile, asyncio, datetime as _dt, time
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("REPORT TEST SKIPPED (нет копии базы)"); sys.exit(0)
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, report as R, tguser
from datetime import date
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
def post(u, d):
    with c.session_transaction() as s: s["csrf"] = "t"
    return c.post(u, data=dict(d, csrf="t"))
assert post("/1c/settings", {"c1_path": p}).status_code == 302
assert post("/1c/sync", {"force": "1"}).status_code == 302
for rr in ["hisobot", "Отчет", "ҳисобот!", "akt sverki", "сверка"]:
    assert R.REPORT_RE.match(rr), rr
assert not R.REPORT_RE.match("отчет керак эмас") and not R.REPORT_RE.match("салом")
with A.app.app_context():
    A.set_settings(tgu_enabled="1", tgu_authorized="1", tgu_api_id="1", tgu_api_hash="x")
    best = None
    for cl in A.q("SELECT * FROM clients WHERE onec_code LIKE '1c77:%' AND active=1"):
        mv = c1.client_moves(R.client_cid(cl))
        if mv and abs(mv[-1]["balance"]) > 10 and len(mv) > 10:
            best = cl; break
    assert best
    last_d = max(m["doc"]["date"] for m in c1.client_moves(R.client_cid(best)) if m["doc"] and m["doc"]["date"])
    st = R.statement(R.client_cid(best), 45, today=last_d)
    # қолдиқ 1С регистри билан бир хил, жамлар мос
    assert abs(st["closing"] + c1.client_moves(R.client_cid(best))[-1]["balance"]) < 0.05 or last_d < date.today()
    assert abs(st["opening"] + st["tot"]["taken"] - st["tot"]["paid"] - st["closing"]) < 0.05, st["tot"]
    for lang in ("uz", "ru"):
        pdf = R.build_pdf(best, st, lang); assert pdf[:5] == b"%PDF-" and len(pdf) > 5000
    A.db().execute("UPDATE clients SET tg_user_id=4242, sms_enabled=1, tg_chat='' WHERE id=?", (best["id"],)); A.db().commit()
r = c.get(f"/clients/{best['id']}/report.pdf"); assert r.status_code == 200 and r.data[:5] == b"%PDF-"
assert "Ҳисобот PDF" in c.get(f"/clients/{best['id']}").get_data(as_text=True)
assert post(f"/clients/{best['id']}/report/send", {}).status_code == 302
with A.app.app_context():
    o = A.q("SELECT * FROM tg_out WHERE kind='report' ORDER BY id DESC LIMIT 1", one=True)
    assert o and o["file"].endswith(".pdf") and os.path.isfile(o["file"]), dict(o) if o else None

# ---- Telegram: мижоз «hisobot» деб ёзади
class M:
    def __init__(s, i, t, out=False): s.id, s.raw_text, s.out = i, t, out; s.date = _dt.datetime.now(_dt.timezone.utc)
class E:
    def __init__(s, i, phone=""): s.id, s.phone, s.bot = i, phone, False
class D:
    def __init__(s, ent, msgs, user=True, name="x"):
        s.entity, s.id, s.is_user, s.is_group, s.name, s._m = ent, ent.id, user, not user, name, msgs
        s.message = max(msgs, key=lambda m: m.id)
class Fake:
    def __init__(s): s.dialogs, s.files, s.texts = [], [], []
    async def is_user_authorized(s): return True
    async def iter_dialogs(s, limit=60):
        for d in s.dialogs: yield d
    async def get_messages(s, ent, min_id=0, limit=15):
        d = next(d for d in s.dialogs if d.entity is ent)
        return [m for m in d._m if m.id > min_id][:limit]
    async def get_input_entity(s, x): return x
    async def send_file(s, ent, path, caption=None, reply_to=None, force_document=False): s.files.append((ent, path, caption, reply_to))
    async def send_message(s, ent, text, reply_to=None): s.texts.append((ent, text))
class FS:
    def __init__(s, fc): s.fc = fc
    def call(s, a, b, fn, timeout=240):          # ҳақиқийдек — алоҳида оқимда (БД га тегмаслиги керак)
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: asyncio.run(fn(s.fc))).result()
fc = Fake(); tguser.set_sender(FS(fc))
with A.app.app_context():
    A.set_settings(report_since=str(time.time() - 60))
    fc.dialogs = [D(E(4242), [M(10, "салом"), M(11, "Hisobot")]), D(E(5555, "998900000001"), [M(3, "отчет")]),
                  D(E(-100777), [M(5, "отчет")], user=False, name="Бегона гуруҳ")]
    assert R.poll(force=True) == 3
    assert len(fc.files) == 1 and fc.files[0][0] == 4242 and fc.files[0][3] == 11 and fc.files[0][1].endswith(".pdf")
    assert len(fc.texts) == 1 and fc.texts[0][0] == 5555           # нотаниш одамга — илтимос қўнғироқ қилинг; гуруҳга жим
    assert R.poll(force=True) == 0                                    # қайта юборилмайди
    fc.dialogs[0]._m.append(M(12, "отчёт")); fc.dialogs[0].message = fc.dialogs[0]._m[-1]
    assert R.poll(force=True) == 1 and len(fc.files) == 2
print("REPORT TESTS PASSED")
