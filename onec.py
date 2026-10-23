# -*- coding: utf-8 -*-
"""Связь с 1С:Предприятие 8.3 через COM (V83.COMConnector) — только ЧТЕНИЕ из 1С.

Как работает:
  1) Python подключается к базе 1С (файловой или серверной) как внешнее соединение;
  2) выполняет запрос 1С (по умолчанию — остатки по счёту расчётов с покупателями, 4010 для Узбекистана);
  3) возвращает строки: Код, Наименование, Долг (+ необязательно Телефон).
В саму базу 1С ничего не записывается.

Требования на ПК с программой (Windows):
  * установлена платформа 1С 8.3 той же разрядности, что и Python (64-бит Python -> 64-бит 1С);
  * зарегистрирован comcntr.dll (от имени администратора):
        regsvr32 "C:\\Program Files\\1cv8\\8.3.XX.XXXX\\bin\\comcntr.dll"
  * пакет pywin32 (ставится сам из requirements.txt при запуске start.bat);
  * у пользователя 1С есть право «Внешнее соединение».
"""
import re, struct, sys, threading
from datetime import datetime, date

PRESETS = {
    "buh_account": {
        "name": "Бухгалтерия: остатки по счёту (4010 — Узбекистан, 62 — РФ)",
        "query": """ВЫБРАТЬ
	Остатки.Субконто1.Код КАК Код,
	ПРЕДСТАВЛЕНИЕ(Остатки.Субконто1) КАК Наименование,
	СУММА(Остатки.СуммаОстатокДт - Остатки.СуммаОстатокКт) КАК Долг
ИЗ
	РегистрБухгалтерии.Хозрасчетный.Остатки(, Счет.Код ПОДОБНО &МаскаСчета, , ) КАК Остатки
СГРУППИРОВАТЬ ПО
	Остатки.Субконто1.Код,
	ПРЕДСТАВЛЕНИЕ(Остатки.Субконто1)""",
    },
    "unf": {
        "name": "УНФ (Управление нашей фирмой): расчёты с покупателями",
        "query": """ВЫБРАТЬ
	Остатки.Контрагент.Код КАК Код,
	ПРЕДСТАВЛЕНИЕ(Остатки.Контрагент) КАК Наименование,
	СУММА(Остатки.СуммаОстаток) КАК Долг
ИЗ
	РегистрНакопления.РасчетыСПокупателями.Остатки КАК Остатки
СГРУППИРОВАТЬ ПО
	Остатки.Контрагент.Код,
	ПРЕДСТАВЛЕНИЕ(Остатки.Контрагент)""",
    },
    "custom": {"name": "Свой запрос 1С", "query": ""},
}

PHONE_QUERY = """ВЫБРАТЬ
	КИ.Ссылка.Код КАК Код,
	КИ.Представление КАК Телефон
ИЗ
	Справочник.Контрагенты.КонтактнаяИнформация КАК КИ
ГДЕ
	КИ.Тип = ЗНАЧЕНИЕ(Перечисление.ТипыКонтактнойИнформации.Телефон)"""

_lock = threading.Lock()


class OneCError(Exception):
    pass


def python_bits():
    return struct.calcsize("P") * 8


def build_conn_string(mode, path, server, ref, user, password):
    """Строка соединения 1С. mode: file | server."""
    esc = lambda s: (s or "").replace('"', '""')
    if mode == "server":
        if not server or not ref:
            raise OneCError("Укажите сервер 1С и имя базы на сервере")
        s = f'Srvr="{esc(server)}";Ref="{esc(ref)}";'
    else:
        if not path:
            raise OneCError("Укажите папку файловой базы 1С (где лежит 1Cv8.1CD)")
        s = f'File="{esc(path)}";'
    if user:
        s += f'Usr="{esc(user)}";'
    if password:
        s += f'Pwd="{esc(password)}";'
    return s


def explain(e):
    """Понятный текст ошибки COM / 1С."""
    t = str(e)
    low = t.lower()
    if isinstance(e, ImportError) or "win32com" in low or "pythoncom" in low:
        return ("Не установлен pywin32 или программа запущена не на Windows. Перезапустите start.bat — пакет поставится сам "
                "(нужен интернет один раз).")
    if "invalid class string" in low or "недопустимая строка с указанием класса" in low or "-2147221005" in t:
        return (f"Компонента V83.COMConnector не зарегистрирована. Python у вас {python_bits()}-битный — нужна 1С той же разрядности. "
                "Запустите командную строку от имени администратора и выполните: "
                'regsvr32 "C:\\Program Files\\1cv8\\<версия>\\bin\\comcntr.dll" '
                "(для 32-битной 1С — путь из C:\\Program Files (x86)\\1cv8).")
    if "идентификация пользователя" in low or "user authentication" in low or "неправильное имя или пароль" in low:
        return "1С не приняла пользователя или пароль. Проверьте логин/пароль пользователя 1С."
    if "внешнее соединение" in low or "external connection" in low:
        return "У пользователя 1С нет права «Внешнее соединение». Включите его в конфигураторе (роль пользователя)."
    if "файл базы данных не обнаружен" in low or "database file not found" in low or "не найден" in low and "1cd" in low:
        return "Не найдена база 1С по указанному пути. Укажите папку, в которой лежит файл 1Cv8.1CD."
    if "заблокирован" in low or "locked" in low or "монопольн" in low:
        return "База 1С занята (монопольный режим, обновление или конфигуратор). Повторите позже."
    if "поле не найдено" in low or "field not found" in low or "таблица не найдена" in low or "синтаксическая ошибка" in low:
        return ("Запрос не подходит к вашей конфигурации 1С: " + t[:300] +
                ". Выберите другой вариант запроса или вставьте свой (поля: Код, Наименование, Долг).")
    return t[:500]


class _Session:
    """Одно COM-подключение к 1С (открывается и закрывается в одном потоке)."""

    def __init__(self, conn_string, connector=None):
        self.conn_string = conn_string
        self.connector = connector
        self.conn = None
        self._co = False

    def __enter__(self):
        if self.connector is None:
            import pythoncom, win32com.client
            pythoncom.CoInitialize(); self._co = True
            self.connector = win32com.client.Dispatch("V83.COMConnector")
        self.conn = self.connector.Connect(self.conn_string)
        return self

    def __exit__(self, *exc):
        self.conn = None
        if self._co:
            import pythoncom
            pythoncom.CoUninitialize()
        return False

    def info(self):
        md = self.conn.Metadata
        return {"name": str(md.Name), "synonym": str(md.Synonym), "version": str(md.Version)}

    def _val(self, v):
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, datetime) or hasattr(v, "year") and hasattr(v, "month"):
            try:
                return date(v.year, v.month, v.day).isoformat()
            except Exception:
                return str(v)
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
        try:
            return str(self.conn.String(v))
        except Exception:
            return str(v)

    def query(self, text, params=None):
        q = self.conn.NewObject("Query")
        q.Text = text
        for k, v in (params or {}).items():
            if k in text:
                q.SetParameter(k, v)
        res = q.Execute()
        cols = res.Columns
        names = [str(cols.Get(i).Name) for i in range(int(cols.Count()))]
        sel = res.Choose()
        out = []
        while sel.Next():
            out.append({n: self._val(sel.Get(i)) for i, n in enumerate(names)})
        return out


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _pick(row, *names):
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def normalize_rows(rows):
    """Строки запроса -> [{code, name, debt, phone}] (одна строка на контрагента, суммы сложены)."""
    acc = {}
    for r in rows:
        name = str(_pick(r, "Наименование", "Контрагент", "Name") or "").strip()
        code = str(_pick(r, "Код", "Code") or "").strip() or name
        if not code:
            continue
        x = acc.setdefault(code, {"code": code, "name": name or code, "debt": 0.0, "phone": ""})
        x["debt"] += _num(_pick(r, "Долг", "Сумма", "Debt"))
        ph = _pick(r, "Телефон", "Phone")
        if ph and not x["phone"]:
            x["phone"] = str(ph).strip()
    for x in acc.values():
        x["debt"] = round(x["debt"], 2)
    return sorted(acc.values(), key=lambda x: -x["debt"])


def fetch(conn_string, query_text, account_mask="4010", with_phones=True, connector=None):
    """Подключиться к 1С и прочитать долги контрагентов. -> (info, rows, warnings)."""
    if not (query_text or "").strip():
        raise OneCError("Пустой запрос 1С")
    if not _lock.acquire(timeout=1):
        raise OneCError("Обмен с 1С уже выполняется, подождите")
    try:
        warns = []
        with _Session(conn_string, connector) as s:
            info = s.info()
            mask = (account_mask or "").strip()
            mask = mask if mask.endswith("%") else mask + "%"
            rows = normalize_rows(s.query(query_text, {"МаскаСчета": mask}))
            if with_phones and rows and not any(r["phone"] for r in rows):
                try:
                    phones = {}
                    for p in s.query(PHONE_QUERY):
                        c = str(p.get("Код") or "").strip()
                        if c and p.get("Телефон") and c not in phones:
                            phones[c] = str(p["Телефон"]).strip()
                    for r in rows:
                        r["phone"] = phones.get(r["code"], "")
                except Exception as e:
                    warns.append("Телефоны из 1С не получены (" + explain(e)[:150] + ") — долги загружены без них.")
        return info, rows, warns
    except OneCError:
        raise
    except Exception as e:
        raise OneCError(explain(e)) from e
    finally:
        _lock.release()


def test_connection(conn_string, connector=None):
    if not _lock.acquire(timeout=1):
        raise OneCError("Обмен с 1С уже выполняется, подождите")
    try:
        with _Session(conn_string, connector) as s:
            return s.info()
    except OneCError:
        raise
    except Exception as e:
        raise OneCError(explain(e)) from e
    finally:
        _lock.release()
