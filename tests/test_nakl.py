"""Накладной расми: чизилади, матн қисқа бўлади, хато бўлса матн тўлиқ қолади. RENT_C1_TEST керак."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("NAKL TEST SKIPPED"); sys.exit(0)
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, naklimg
with A.app.app_context():
    A.set_settings(c1_path=p)
    j = c1.journal()
    docs = sorted((v for v in j.values() if v["kind"] == 210 and not v["deleted"] and v["date"]), key=lambda v: (v["date"], v["time"]), reverse=True)
    v = next(v for v in docs[:200] if c1.sale_doc(v["id"]) and len(c1.sale_doc(v["id"])["lines"]) >= 3)
    for lang in ("ru", "uz"):
        path = c1.invoice_image(v["id"], dict(phone="+998 90 111 22 33", lang=lang, id=0, name="Т"), tmp)
        assert path and os.path.getsize(path) > 5000, path
        from PIL import Image
        im = Image.open(path); assert im.width == naklimg.W * naklimg.SCALE and im.height > 300
    cl = dict(id=0, name="Тест", lang="ru", phone="")
    rows = [dict(iddoc=v["id"], cid="", kind=210, amt=0.0, doc_no=v["no"])]
    full = c1.doc_message(cl, rows)
    short = c1.doc_message(cl, rows, brief=True)
    assert "•" in full and "•" not in short and f"№{str(v['no']).strip()}" in short.replace("№ ", "№"), short
    assert len(short) < 1000
    # хато бўлса — None
    assert c1.invoice_image("NOPE", None, tmp) is None
    w, h, ops = naklimg.layout(c1.sale_doc(v["id"]), prev=10, after=20, lang="uz", rate=12000, rate_date="01.01.2026")
    assert any(o[0] == "text" and "Kurs" in o[3] for o in ops)
print("NAKL TESTS PASSED")
