"""Документация: матндан, PDF'дан, расмдан ва Word'дан (RENT_DOCX_CMD) бланкли PDF, рақамлаш, журнал, бланк созламаси."""
import os, sys, tempfile, io
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
td = tempfile.mkdtemp()
fake = os.path.join(td, "fake_word.py")
open(fake, "w").write("import sys, shutil; shutil.copy(sys.argv[1] + '.pdf', sys.argv[2])\n")   # тест: .docx ёнида тайёр PDF турибди
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, blank
from pypdf import PdfReader
A.app.testing = True
c = A.app.test_client()
def post(u, d, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    return c.post(u, data=dict(d, csrf="t"), **kw)
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
assert c.get("/docs").status_code == 200 and c.get("/docs/new?mode=text").status_code == 200 and c.get("/docs/blank").status_code == 200
r = c.get("/docs/blank/sample.pdf"); assert r.status_code == 200 and r.data[:4] == b"%PDF" and r.headers["X-Frame-Options"] == "SAMEORIGIN"
# 1) матн
r = post("/docs/new", {"mode": "text", "kind": "kp", "date": "2026-10-19", "title": "Коммерческое предложение", "to_text": "Кому: ООО «Тест»",
                       "body": "Текст **жирный**.\n\n# Раздел\n- пункт\n\nА | Б\n1 | 2", "sign": "1"})
assert r.status_code == 302, r.data[:300]
with A.app.app_context():
    d1 = A.q("SELECT * FROM doc_files ORDER BY id DESC LIMIT 1", one=True)
assert d1["no"] == "19-10" and os.path.isfile(d1["pdf_path"])
txt = PdfReader(d1["pdf_path"]).pages[0].extract_text()
assert "19-10" in txt and "Коммерческое предложение" in txt and "+998 33 409-60-70" in txt, txt[:400]
post("/docs/new", {"mode": "text", "kind": "kp", "date": "2026-11-01", "title": "КП 2", "body": "x"})
with A.app.app_context():
    assert A.q("SELECT no FROM doc_files ORDER BY id DESC LIMIT 1", one=True)["no"] == "01-11"
# 2) PDF юклаш — саҳифалар сони сақланади, бланк қўшилади
src = blank.render_text("Чужой договор", "Текст\n" * 200, no="X", sign=False)
n_src = len(PdfReader(io.BytesIO(src)).pages)
r = post("/docs/new?mode=file", {"mode": "file", "kind": "contract", "date": "2026-10-19", "title": "Договор поставки",
                                  "file": (io.BytesIO(src), "dogovor.pdf")}, content_type="multipart/form-data")
assert r.status_code == 302
with A.app.app_context():
    d2 = A.q("SELECT * FROM doc_files ORDER BY id DESC LIMIT 1", one=True)
assert d2["no"] == "19-10" and d2["orig_name"] == "dogovor.pdf" and os.path.isfile(d2["orig_path"])
assert len(PdfReader(d2["pdf_path"]).pages) == n_src and "409-60-70" in PdfReader(d2["pdf_path"]).pages[0].extract_text()
# 3) расм
import struct, zlib
def png(w=40, h=30):
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
r = post("/docs/new?mode=file", {"mode": "file", "kind": "act", "date": "2026-10-19", "file": (io.BytesIO(png()), "akt.png")}, content_type="multipart/form-data")
with A.app.app_context():
    d3 = A.q("SELECT * FROM doc_files ORDER BY id DESC LIMIT 1", one=True)
assert d3["no"] == "19-10" and len(PdfReader(d3["pdf_path"]).pages) == 1
# 4) Word (тестда — сохта ўгирувчи)
os.environ["RENT_DOCX_CMD"] = f"{sys.executable} {fake}"
import docflow
orig = docflow.word_to_pdf
def fake_word(a, b):
    open(a + ".pdf", "wb").write(src); orig(a, b)
docflow.word_to_pdf = fake_word
r = post("/docs/new?mode=file", {"mode": "file", "kind": "letter", "date": "2026-10-19", "file": (io.BytesIO(b"PK fake docx"), "pismo.docx")}, content_type="multipart/form-data")
with A.app.app_context():
    d4 = A.q("SELECT * FROM doc_files ORDER BY id DESC LIMIT 1", one=True)
assert d4["no"] == "19-10" and d4["pdf_path"] and os.path.isfile(d4["pdf_path"]), d4
# нотўғри тур
r = post("/docs/new?mode=file", {"mode": "file", "kind": "other", "file": (io.BytesIO(b"MZ"), "virus.exe")}, content_type="multipart/form-data", follow_redirects=True)
assert "не поддерживается" in r.get_data(as_text=True)
# журнал, қидирув, карточка, юклаб олиш
h = c.get("/docs").get_data(as_text=True); assert "19-10" in h and "01-11" in h
h = c.get("/docs?q=поставки").get_data(as_text=True); assert "Договор поставки" in h and "01-11" not in h
# шу куни шу турдан иккинчиси — «-2»
post("/docs/new", {"mode": "text", "kind": "kp", "date": "2026-11-01", "title": "КП 3", "body": "y"})
with A.app.app_context():
    assert A.q("SELECT no FROM doc_files ORDER BY id DESC LIMIT 1", one=True)["no"] == "01-11-2"
assert "iframe" in c.get(f"/docs/{d1['id']}").get_data(as_text=True)
r = c.get(f"/docs/{d2['id']}/file/pdf?dl=1"); assert r.status_code == 200 and r.data[:4] == b"%PDF"
assert c.get(f"/docs/{d2['id']}/file/orig").status_code == 200 and c.get(f"/docs/{d2['id']}/file/xyz").status_code == 404
# таҳрирлаш — рақам ўзгармайди, PDF қайта қурилади
post(f"/docs/{d1['id']}/edit", {"kind": "kp", "date": "2026-10-19", "title": "КП изменено", "body": "Новый текст", "sign": "1"})
with A.app.app_context():
    e = A.q("SELECT * FROM doc_files WHERE id=?", (d1["id"],), one=True)
assert e["no"] == "19-10" and "КП изменено" in PdfReader(e["pdf_path"]).pages[0].extract_text()
# бланк созламаси -> қайта қуриш
post("/docs/blank", {"name": "Lumberture", "legal": "ООО «Тест Лумбертур»", "requisites": "ИНН 123", "signer_title": "Директор", "signer_name": "Иванов И.И.",
                     "phone": "+998 33 409-60-70"})
post(f"/docs/{d1['id']}/rebuild", {})
with A.app.app_context():
    e = A.q("SELECT * FROM doc_files WHERE id=?", (d1["id"],), one=True)
t = PdfReader(e["pdf_path"]).pages[0].extract_text(); assert "ООО «Тест Лумбертур»" in t and "Иванов И.И." in t, t[-300:]
# ўчириш — фақат админ
post(f"/docs/{d3['id']}/delete", {})
assert f"/docs/{d3['id']}\"" not in c.get("/docs").get_data(as_text=True)
print("DOCFLOW OK")
