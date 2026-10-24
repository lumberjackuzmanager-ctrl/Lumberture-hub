"""Интерфейс тили: ru (стандарт) / uz (лотин) / en. Ҳамма саҳифа уч тилда очилади; маълумотлар (товар, мижоз номлари) ва
чоп этиш таржима қилинмайди."""
import os, re, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, i18n
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok()
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
UZC = re.compile("[ЎўҚқҒғҲҳ]")

# транслитерация
assert i18n.translit("Ўчиришга белгиланганлар") == "Oʻchirishga belgilanganlar"
assert i18n.translit("Ҳужжатлар") == "Hujjatlar" and i18n.translit("Етказиб берувчи") == "Yetkazib beruvchi"
assert i18n.translit("ЖАМИ") == "JAMI" and i18n.translit("қарз") == "qarz"

# луғат: шаблон бўлаклари, хабарлар ичидаги бўлаклар
i18n._D.update({"Сақлаш": {"ru": "Сохранить", "uz": "Saqlash", "en": "Save"},
                "Мижоз танланмаган": {"ru": "Клиент не выбран", "uz": "Mijoz tanlanmagan", "en": "No client selected"},
                "-қатор: товар танланмаган": {"ru": "-я строка: товар не выбран", "uz": "-qator: tovar tanlanmagan", "en": "row: no item selected"}})
i18n._SUB = sorted((k for k in i18n._D if len(k) >= 4), key=len, reverse=True); i18n._cache.clear()
assert i18n.text('<button class="btn">Сақлаш</button>', "ru") == '<button class="btn">Сохранить</button>'
assert i18n.text(' Сақлаш ', "en") == ' Save '
assert i18n.tr("3-қатор: товар танланмаган", "ru") == "3-я строка: товар не выбран"
assert i18n.tr("Мижоз танланмаган", "en") == "No client selected"

# ҳамма саҳифалар уч тилда
skip = {"logout", "set_lang", "static"}
urls = []
for r in A.app.url_map.iter_rules():
    if "GET" in r.methods and not r.arguments and r.endpoint not in skip and not r.rule.startswith("/api") and "download" not in r.endpoint \
            and not r.rule.endswith((".pdf", ".xlsx", ".csv", ".png", ".css", ".ico", ".txt")):
        urls.append(r.rule)
bad = []
js_scripts = []
for lg in ("uz", "en", "ru"):
    assert c.get(f"/lang/{lg}?next=/").status_code == 302
    with A.app.app_context():
        assert A.q("SELECT ui_lang FROM users WHERE username='admin'", one=True)[0] == lg
    for u in urls:
        r = c.get(u)
        if r.status_code >= 500:
            bad.append((lg, u, r.status_code))
        if r.status_code == 200 and r.mimetype == "text/html":     # таржимадан кейин скрипт бузилмасин (апостроф: «Eman'da» — 24.10)
            for js in re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", r.get_data(as_text=True), flags=re.S):
                js_scripts.append((lg, u, js))
        if lg == "uz" and r.status_code == 200 and r.mimetype == "text/html":
            h = r.get_data(as_text=True)
            h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
            for w in sorted(i18n.DATA_WORDS, key=len, reverse=True):     # фойдаланувчи маълумоти (ҳисоблар, справочник) — таржима қилинмайди
                h = h.replace(w, "")
            m = UZC.search(h)
            if m:
                bad.append((lg, u, "кирилл: " + h[max(0, m.start() - 60):m.start() + 40]))
assert not bad, bad[:10]
import shutil, subprocess, json as _json
if shutil.which("node"):
    chk = "const a=JSON.parse(require('fs').readFileSync(0,'utf8'));const e=[];for(const [l,u,j] of a){try{new Function(j)}catch(x){e.push([l,u,x.message])}}console.log(JSON.stringify(e))"
    seen, uniq = set(), []
    for lg, u, js in js_scripts:
        if (lg, js) not in seen:
            seen.add((lg, js)); uniq.append([lg, u, js])
    out = subprocess.run(["node", "-e", chk], input=_json.dumps(uniq), capture_output=True, text=True, timeout=120)
    errs = _json.loads(out.stdout or "[]")
    assert not errs, errs[:10]
h = c.get("/lang/uz?next=/kassa").status_code
assert 'lang="uz"' in c.get("/").get_data(as_text=True)
# тил кирмасдан аввал ҳам (логин саҳифаси)
c2 = A.app.test_client()
assert c2.get("/lang/en?next=/login").status_code == 302 and 'lang="en"' in c2.get("/login").get_data(as_text=True)
print("I18N OK", len(urls), "pages")
