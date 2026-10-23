"""Тесты связи с 1С (COM заменён поддельным объектом — 1С не нужна).  python tests/test_onec.py"""
import os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ["RENT_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
with A.app.app_context():                       # тестларда «cashier» — аввалгидек кенг роль (созламалардан ташқари ҳаммаси, проведен қилади)
    A.db().execute("UPDATE roles SET kind='full', sections='[]', can_post=1 WHERE key='cashier'"); A.db().commit()
    import roles as _roles_t; _roles_t.reload()
import onec

# ---------------- поддельный V83.COMConnector
class Col:
    def __init__(self, n): self.Name = n
class Cols:
    def __init__(self, names): self.n = names
    def Count(self): return len(self.n)
    def Get(self, i): return Col(self.n[i])
class Sel:
    def __init__(self, rows): self.rows, self.i = rows, -1
    def Next(self): self.i += 1; return self.i < len(self.rows)
    def Get(self, i): return self.rows[self.i][i]
class Res:
    def __init__(self, names, rows): self.Columns, self.rows = Cols(names), rows
    def Choose(self): return Sel(self.rows)
class Query:
    def __init__(self, conn): self.conn, self.Text, self.params = conn, "", {}
    def SetParameter(self, k, v): self.params[k] = v
    def Execute(self):
        self.conn.queries.append((self.Text, dict(self.params)))
        if "КонтактнаяИнформация" in self.Text:
            if FAKE["phones_fail"]: raise Exception("Поле не найдено \"КИ.Тип\"")
            return Res(["Код", "Телефон"], FAKE["phones"])
        if FAKE["query_fail"]: raise Exception(FAKE["query_fail"])
        return Res(["Код", "Наименование", "Долг"], FAKE["debts"])
class MD: Name, Synonym, Version = "БухгалтерияДляУзбекистана", "Бухгалтерия для Узбекистана", "3.0.15.2"
class Conn:
    def __init__(self): self.Metadata, self.queries = MD(), []
    def NewObject(self, t): assert t == "Query"; return Query(self)
    def String(self, v): return str(v)
class Connector:
    def __init__(self): self.last = None
    def Connect(self, s):
        if FAKE["connect_fail"]: raise Exception(FAKE["connect_fail"])
        self.last = s; self.conn = Conn(); return self.conn
CON = Connector()
FAKE = dict(debts=[], phones=[], phones_fail=False, query_fail="", connect_fail="")
_orig_init = onec._Session.__init__
onec._Session.__init__ = lambda self, cs, connector=None: _orig_init(self, cs, connector or CON)

# ---------------- чистые функции
assert onec.build_conn_string("file", r"C:\1C\Buh", "", "", "Админ", 'p"w') == 'File="C:\\1C\\Buh";Usr="Админ";Pwd="p""w";'
assert onec.build_conn_string("server", "", "srv", "buh", "", "") == 'Srvr="srv";Ref="buh";'
for bad in (("file", "", "", "", "", ""), ("server", "", "srv", "", "", "")):
    try: onec.build_conn_string(*bad); raise AssertionError
    except onec.OneCError: pass
rows = onec.normalize_rows([{"Код": "001", "Наименование": "Мебель Плюс", "Долг": 1000.5}, {"Код": "001", "Наименование": "Мебель Плюс", "Долг": "2 000,5"},
                            {"Код": "", "Наименование": "Без кода", "Долг": 5}])
assert rows[0] == {"code": "001", "name": "Мебель Плюс", "debt": 3001.0, "phone": ""} and rows[1]["code"] == "Без кода"
assert "regsvr32" in onec.explain(Exception("(-2147221005, 'Invalid class string', None, None)"))
assert "пароль" in onec.explain(Exception("Идентификация пользователя не выполнена"))

c = A.app.test_client(); A.app.testing = True
def csrf():
    with c.session_transaction() as s:
        s.setdefault("csrf", "t"); return s["csrf"]
def post(url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = csrf(); return c.post(url, data=d, **kw)
assert post("/login", {"username": "admin", "password": "admin123"}).status_code == 302

# ---------------- страница и меню
h = c.get("/admin/onec").get_data(as_text=True)
assert "Связь с 1С" in h and "4010" in h
assert "/1c/settings" in c.get("/clients").get_data(as_text=True)

# без настроек — понятная ошибка
h = post("/admin/onec", {"action": "test"}, follow_redirects=True).get_data(as_text=True)
assert "Укажите папку файловой базы" in h

# ---------------- сохранить настройки (пароль не показывается)
post("/admin/onec", {"action": "save", "onec_mode": "file", "onec_path": r"C:\1C\Buh", "onec_user": "Админ", "onec_password": "secret1",
                     "onec_preset": "buh_account", "onec_account": "4010", "onec_auto_minutes": "30"})
with A.app.app_context():
    assert A.get_setting("onec_password") == "secret1" and A.get_setting("onec_auto_minutes") == "30"
h = c.get("/admin/onec").get_data(as_text=True)
assert "secret1" not in h and "сохранён" in h
post("/admin/onec", {"action": "save", "onec_mode": "file", "onec_path": r"C:\1C\Buh", "onec_user": "Админ", "onec_password": "",
                     "onec_preset": "buh_account", "onec_account": "4010", "onec_auto_minutes": "0"})
with A.app.app_context():
    assert A.get_setting("onec_password") == "secret1"   # пустое поле — пароль не стирается

# ---------------- проверка подключения
h = post("/admin/onec", {"action": "test"}).get_data(as_text=True)
assert "Подключение к 1С работает" in h and "Бухгалтерия для Узбекистана" in h
assert CON.last == 'File="C:\\1C\\Buh";Usr="Админ";Pwd="secret1";'
FAKE["connect_fail"] = "(-2147221005, 'Invalid class string', None, None)"
h = post("/admin/onec", {"action": "test"}).get_data(as_text=True)
assert "regsvr32" in h; FAKE["connect_fail"] = ""

# ---------------- данные: один клиент уже есть в платформе (по имени), один новый, один с нулём
with A.app.app_context():
    A.db().execute("INSERT INTO clients(name,phone,tg_code,created_at,sms_enabled) VALUES('Мебель Плюс','',?,?,1)", (A.new_code(), A.now()))
    cid1 = A.q("SELECT id FROM clients WHERE name='Мебель Плюс'", one=True)["id"]
    A.db().execute("INSERT INTO client_debts(client_id,doc_date,due_date,kind,amount,created_at) VALUES(?,?,?,?,?,?)", (cid1, "2026-09-01", "2026-09-10", "services", 1_000_000, A.now()))
    A.db().commit()
FAKE["debts"] = [["001", "Мебель Плюс", 1_500_000.0], ["002", "ООО Шкаф", 2_300_000.4], ["003", "Без долга", 0.0], ["004", "Аванс", -50_000.0]]
FAKE["phones"] = [["002", "+998 90 123 45 67"], ["001", "+998 99 000 00 00"]]

h = post("/admin/onec", {"action": "preview"}).get_data(as_text=True)
assert "Предпросмотр" in h and "новый клиент" in h and "по имени" in h and "+500 000" in h and "+2 300 000" in h
assert "Без долга" not in h and "Аванс" not in h               # новых без долга не заводим
q_text, params = CON.conn.queries[0]
assert "Хозрасчетный.Остатки" in q_text and params == {"МаскаСчета": "4010%"}
with A.app.app_context():                                       # предпросмотр ничего не пишет
    assert A.scalar("SELECT COUNT(*) FROM clients") == 1 and A.scalar("SELECT COUNT(*) FROM client_debts WHERE onec=1") == 0

r = post("/admin/onec", {"action": "sync"})
assert r.status_code == 302
with A.app.app_context():
    bal = A.client_balances()
    new = A.q("SELECT * FROM clients WHERE onec_code='002'", one=True)
    old = A.q("SELECT * FROM clients WHERE id=?", (cid1,), one=True)
    assert new and new["name"] == "ООО Шкаф" and new["sms_enabled"] == 0 and new["phone"] == "+998 90 123 45 67"
    assert old["onec_code"] == "001" and old["phone"] == "+998 99 000 00 00" and old["sms_enabled"] == 1
    assert round(bal[cid1]) == 1_500_000 and round(bal[new["id"]]) == 2_300_000
    assert A.scalar("SELECT COUNT(*) FROM clients") == 2
    assert A.scalar("SELECT COUNT(*) FROM notifications") == 0      # сообщений нет
    assert "новых клиентов: 1" in A.get_setting("onec_last_result")
    assert A.client_overdue(cid1)[0] > 0                              # старый долг остаётся просроченным

# повторная загрузка — ничего не меняется
post("/admin/onec", {"action": "sync"})
with A.app.app_context():
    assert A.scalar("SELECT COUNT(*) FROM client_debts WHERE onec=1") == 2 and A.scalar("SELECT COUNT(*) FROM client_payments WHERE onec=1") == 0

# в 1С оплатили часть, второй контрагент исчез (долг 0) -> оплаты «Сверка с 1С»
FAKE["debts"] = [["001", "Мебель Плюс", 400_000.0]]
post("/admin/onec", {"action": "sync"})
with A.app.app_context():
    bal = A.client_balances()
    assert round(bal[cid1]) == 400_000 and round(bal[new["id"]]) == 0
    p = A.q("SELECT * FROM client_payments WHERE client_id=? AND onec=1", (cid1,), one=True)
    assert p["amount"] == 1_100_000 and p["uzs_part"] == 1_100_000 and "Сверка с 1С" in p["note"]

# ошибка телефонов не ломает загрузку; ошибка запроса — понятный текст
FAKE["phones_fail"] = True
h = post("/admin/onec", {"action": "preview"}).get_data(as_text=True)
assert "Телефоны из 1С не получены" in h
FAKE["query_fail"] = "Поле не найдено \"Остатки.Субконто1\""
h = post("/admin/onec", {"action": "preview"}).get_data(as_text=True)
assert "Запрос не подходит к вашей конфигурации" in h
FAKE["query_fail"] = ""; FAKE["phones_fail"] = False

# свой запрос
post("/admin/onec", {"action": "save", "onec_mode": "server", "onec_server": "srv", "onec_ref": "ut", "onec_preset": "custom",
                     "onec_query": "ВЫБРАТЬ 1 КАК Код", "onec_account": "", "onec_auto_minutes": "0"})
post("/admin/onec", {"action": "preview"})
assert CON.last.startswith('Srvr="srv";Ref="ut";') and CON.conn.queries[0][0] == "ВЫБРАТЬ 1 КАК Код"

# ---------------- автозагрузка по расписанию
from datetime import datetime, timedelta
with A.app.app_context():
    A.set_settings(onec_auto_minutes="30", onec_last_at=(datetime.now() - timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S"))
    assert A.onec_due(datetime.now())
    A.set_settings(onec_last_at=A.now())
    assert not A.onec_due(datetime.now())
    A.set_settings(onec_auto_minutes="0", onec_last_at="")
    assert not A.onec_due(datetime.now())

# ---------------- права: кассир не видит страницу 1С
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    A.db().execute("INSERT INTO users(username,password_hash,full_name,role) VALUES('kassa2',?,'К','cashier')", (generate_password_hash("Kassa2026x"),)); A.db().commit()
c.get("/logout"); post("/login", {"username": "kassa2", "password": "Kassa2026x"})
assert c.get("/admin/onec").status_code == 403
print("1C TESTS PASSED")
