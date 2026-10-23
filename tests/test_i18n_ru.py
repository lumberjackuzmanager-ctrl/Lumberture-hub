"""Рус тилида платформа саҳифаларида ўзбекча (кирилл) матн қолмаслиги керак: янги матн қўшилса — lang/ui.json'га 3 тилда."""
import os, re, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, i18n
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
UZ = re.compile("[ЎўҚқҒғҲҳ]")
bad = []
for lg in ("ru", "en"):
    c.get(f"/lang/{lg}?next=/")
    for u in ("/erp", "/erp?kind=pay", "/erp?kind=receipt", "/erp/sale/new", "/erp/pay/new2", "/erp/receipt/new2", "/erp/reval/new2", "/erp/goods?pick=1",
              "/erp/rep/stock", "/erp/rep/stock?mode=only", "/erp/rep/sales", "/erp/rep/receipts", "/erp/rep/mutual", "/erp/rep/mutual?mode=bal", "/kassa", "/erp/catalog", "/erp/catalog/decors", "/erp/epay", "/kassa/raspil", "/admin/appearance", "/staff", "/staff/new", "/kassa/raspil/settings", "/kassa/staff", "/kassa/items", "/ready", "/admin/connections", "/docs", "/docs/new?mode=text", "/docs/new?mode=file", "/docs/blank"):
        h = c.get(u).get_data(as_text=True)
        h = re.sub(r"//[^\n]*|/\*.*?\*/|<!--.*?-->", "", h, flags=re.S)
        for w in sorted(i18n.DATA_WORDS, key=len, reverse=True):
            h = h.replace(">" + w + "<", "><")
        for m in i18n.SEG.finditer(h):
            if UZ.search(m.group()):
                bad.append((lg, u, m.group().strip()[:80]))
assert not bad, bad[:10]
print("I18N RU/EN OK")
