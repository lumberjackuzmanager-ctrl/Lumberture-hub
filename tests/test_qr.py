"""Накладнойдаги QR: печатда бор; «тайёр»/цех-бот расмдан QR орқали накладнойни топади (OCR'сиз)."""
import os, sys, tempfile, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, ready as R
u = erp.print_qr("006343", datetime.date(2026, 10, 19))
assert u.startswith("data:image/svg+xml")
import segno
q = segno.make("LB:6343:20261019", error="m", micro=False)
p = os.path.join(tempfile.mkdtemp(), "photo.png"); q.save(p, scale=6, border=6)
assert R.qr_codes(p) == ["LB:6343:20261019"]
assert R.ocr_image(p).startswith("QR:LB:6343:20261019")
calls = []
def fd(no, days=180, on=None):
    calls.append((no, on))
    return dict(iddoc="X1", no="00006343", date=on) if on == datetime.date(2026, 10, 19) else None
R.find_doc = fd
d, st, info = R.pick_doc("", R.ocr_image(p))
assert st == "ok" and d["iddoc"] == "X1" and info == "QR орқали" and calls[0] == ("6343", datetime.date(2026, 10, 19))
d, st, info = R.pick_doc("6343", "")                  # ишчи рақам ёзса — олдингидек
print("QR OK")
