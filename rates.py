# -*- coding: utf-8 -*-
"""Курс доллара (USD → UZS).

Источники (по порядку):
  1. Telegram-бот @em_mat_info_bot (им пользуется владелец). Бот отвечает на команду /rate_usd одной строкой:
         1$ = 11 840 UZS
     Запрос идёт с подключённого Telegram-аккаунта платформы (Telethon), см. tguser.Sender.ask_bot.
  2. Резерв — официальный курс ЦБ Узбекистана: https://cbu.uz/ru/arkhiv-kursov-valyut/json/USD/
  3. Ручной ввод администратором.
"""
import re
from datetime import datetime

CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/USD/"
RATE_MIN, RATE_MAX = 3000.0, 100000.0          # правдоподобный диапазон сум за 1 $
PICKS = {"sell": "Продажа (если бот даёт покупку и продажу)", "buy": "Покупка", "avg": "Среднее покупки и продажи"}

_NUM = re.compile(r"\d{1,3}(?:[ \u00a0\u202f'’]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_USD = re.compile(r"\$|usd|доллар|dollar|\bдол\b", re.I)
_BUY = re.compile(r"покуп|buy|olish|сотиб|xarid", re.I)
_SELL = re.compile(r"прода|sell|sotish|сотиш", re.I)
_OTHER = re.compile(r"€|eur|евро|₽|rub|руб|£|gbp|¥|cny|юан|kzt|тенге", re.I)


class RateError(Exception):
    pass


def _nums(s):
    out = []
    for m in _NUM.findall(s):
        v = m.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace("'", "").replace("’", "")
        try:
            out.append(float(v.replace(",", ".")))
        except ValueError:
            pass
    return [v for v in out if RATE_MIN <= v <= RATE_MAX]


def parse_rate(text, pick="sell"):
    """Курс USD→UZS из ответа бота. -> float или None.
    «1$ = 11 840 UZS» -> 11840.  «USD покупка 12 650 / продажа 12 700» -> 12700 (sell) / 12650 (buy) / 12675 (avg)."""
    if not text:
        return None
    lines = [l for l in re.split(r"[\r\n]+", text) if l.strip()]
    usd_lines = [l for l in lines if _USD.search(l) and not _OTHER.search(l)]
    if not any(_nums(l) for l in usd_lines):           # «USD» может быть заголовком, а цифры — строками ниже
        idx = next((i for i, l in enumerate(lines) if _USD.search(l)), None)
        usd_lines = ([l for l in lines[idx:idx + 4] if not _OTHER.search(l)] if idx is not None
                     else [l for l in lines if not _OTHER.search(l)])
    block = "\n".join(usd_lines)
    m_b = re.search(r"(?:покуп\w*|buy|olish)[^\d\n]{0,15}(" + _NUM.pattern + ")", block, re.I)
    m_s = re.search(r"(?:прода\w*|sell|sotish)[^\d\n]{0,15}(" + _NUM.pattern + ")", block, re.I)
    buy = (_nums(m_b.group(1)) or [None])[0] if m_b else None
    sell = (_nums(m_s.group(1)) or [None])[0] if m_s else None
    if not (buy and sell):                            # «12 650 покупка» — подпись после числа, по строкам
        for l in usd_lines:
            n = _nums(l)
            if n and _BUY.search(l) and not _SELL.search(l) and buy is None:
                buy = n[0]
            elif n and _SELL.search(l) and not _BUY.search(l) and sell is None:
                sell = n[0]
    if buy and sell:
        return {"buy": buy, "sell": sell}.get(pick, round((buy + sell) / 2, 2))
    if buy or sell:
        return buy or sell
    n = _nums(block)
    return n[0] if n else None


def fetch_cbu(timeout=15, get=None):
    """Официальный курс ЦБ РУз. -> (курс, 'YYYY-MM-DD'). RateError при ошибке."""
    if get is None:
        import requests
        get = requests.get
    try:
        r = get(CBU_URL, timeout=timeout)
        data = r.json()
        row = next(x for x in data if x.get("Ccy") == "USD")
        rate = float(str(row["Rate"]).replace(",", "."))
        d = datetime.strptime(row.get("Date", ""), "%d.%m.%Y").date().isoformat() if row.get("Date") else None
    except Exception as e:  # noqa — сеть, формат, JSON
        raise RateError(f"ЦБ (cbu.uz) недоступен: {e.__class__.__name__}: {str(e)[:120]}") from e
    if not (RATE_MIN <= rate <= RATE_MAX):
        raise RateError(f"ЦБ вернул странный курс: {rate}")
    return rate, d
