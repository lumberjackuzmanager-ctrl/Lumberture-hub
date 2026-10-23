# -*- coding: utf-8 -*-
"""Демо-данные: мебельщики и маляры (помечены «демо», удаляются кнопкой в «Настройки»).
Вызывается из админки (Настройки → «Создать демо-данные») внутри контекста приложения."""
from datetime import date
import app as A

TENANTS = [  # название, категория, помещение, м², ставка $/м², телефон, мусор (сум), свет: нач., авг, сен; оплаты [(дата, сумма, валюта, способ)]
    ("Мебельный цех «Дилшод»", "Мебельщик", "Цех №1", 120, 4, "+998 90 123 45 67", 50_000, (12500, 14300, 16250),
     [("2026-08-05", 505, "USD", "transfer"), ("2026-08-05", 650_000, "UZS", "transfer"), ("2026-09-04", 505, "USD", "transfer"),
      ("2026-09-04", 2_450_000, "UZS", "transfer"), ("2026-10-05", 505, "USD", "transfer"), ("2026-10-05", 2_600_000, "UZS", "transfer")]),
    ("ИП Рахимов Б. (корпусная мебель)", "Мебельщик", "Цех №2", 80, 4, "+998 93 555 12 34", 50_000, (8020, 9220, 10480),
     [("2026-08-10", 4_550_000, "UZS", "cash"), ("2026-09-12", 5_500_000, "UZS", "cash"), ("2026-10-07", 3_000_000, "UZS", "cash")]),
    ("Мягкая мебель «Комфорт»", "Мебельщик", "Цех №3", 95, 4, "+998 97 700 88 99", 50_000, (3100, 4050, 5200),
     [("2026-08-08", 405, "USD", "card"), ("2026-08-08", 525_000, "UZS", "card"), ("2026-09-20", 300, "USD", "card")]),
    ("Покрасочная «Колор»", "Маляр", "Покрасочная камера №1", 60, 5, "+998 99 321 00 11", 80_000, (20400, 23300, 26100),
     [("2026-08-06", 300, "USD", "transfer"), ("2026-08-06", 380_000, "UZS", "transfer"), ("2026-09-06", 300, "USD", "transfer"),
      ("2026-09-06", 3_280_000, "UZS", "transfer"), ("2026-10-06", 300, "USD", "transfer"), ("2026-10-06", 3_180_000, "UZS", "transfer")]),
    ("Маляр Юсупов Ж.", "Маляр", "Покрасочная камера №2", 45, 5, "+998 91 444 22 33", 80_000, (5600, 7400, 9300),
     [("2026-08-15", 2_000_000, "UZS", "cash")]),
    ("Склад фурнитуры «Мир фурнитуры»", "Другое", "Склад №1", 50, 3.5, "+998 95 600 70 80", 30_000, (900, 1150, 1380),
     [("2026-08-03", 175, "USD", "transfer"), ("2026-08-03", 280_000, "UZS", "transfer"), ("2026-09-02", 200, "USD", "transfer"),
      ("2026-09-02", 530_000, "UZS", "transfer"), ("2026-10-02", 200, "USD", "transfer")]),
]
OPENING = {  # начальный долг (перенос из старого учёта) на 31.07.2026: ($, сум, комментарий); минус — аванс
    "Маляр Юсупов Ж.": (150, 400_000, "долг с прошлого учёта (тетрадь)"),
    "Мягкая мебель «Комфорт»": (0, 250_000, "свет за июль"),
    "Склад фурнитуры «Мир фурнитуры»": (-50, 0, "аванс, внесён до перехода на программу"),
}
DEMO_RATE = 11840          # демо-курс, если курс ещё не загружался (помечен «демо», удаляется вместе с демо-данными)


def create(conn):
    q, now = A.q, A.now
    if q("SELECT 1 FROM tenants WHERE is_demo=1", one=True):
        return create_clients(conn)
    if not A.fx_current():
        A.fx_store(DEMO_RATE, "manual", raw="демо-курс", is_demo=1)
    el, trash, rent, comp = A.ct_by_code("electricity"), A.ct_by_code("trash"), A.ct_by_code("rent"), A.ct_by_code("compressor")
    if not A.tariff_for(rent["id"], date(2026, 8, 1)):
        conn.execute("INSERT INTO tariffs(charge_type_id,valid_from,price,created_at,is_demo) VALUES(?,?,?,?,1)",
                     (rent["id"], "2026-01-01", 4, now()))
    if not A.tariff_for(el["id"], date(2026, 8, 1)):
        conn.execute("INSERT INTO tariffs(charge_type_id,valid_from,price,created_at,is_demo) VALUES(?,?,?,?,1)",
                     (el["id"], "2026-01-01", 1000, now()))
    guard = conn.execute("INSERT INTO charge_types(name,kind,default_amount,apply_all,unit,sort,is_demo,currency) VALUES(?,?,?,?,?,?,1,'UZS')",
                         ("Охрана территории", "area", 5000, 1, "м²", 5)).lastrowid
    for (name, cat, unit, area, rate, phone, tr, (m0, m8, m9), pays) in TENANTS:
        tid = conn.execute("INSERT INTO tenants(name,unit,area,phone,category,rent_amount,rent_rate,contract_start,notes,active,tg_code,is_demo,created_at) "
                           "VALUES(?,?,?,?,?,0,?,?,?,1,?,1,?)",
                           (name, unit, area, phone, cat, None if rate == 4 else rate, "2026-03-01", "Демо-арендатор", A.new_code(), now())).lastrowid
        if name in UZ_LANG:      # пример: уведомления на узбекском (латиница)
            conn.execute("UPDATE tenants SET lang='uz' WHERE id=?", (tid,))
        if cat == "Мебельщик":   # компрессор нужен только мебельщикам ($25 в месяц)
            conn.execute("INSERT INTO tenant_fixed(tenant_id,charge_type_id,enabled,amount) VALUES(?,?,1,?)", (tid, comp["id"], 25))
        conn.execute("INSERT INTO tenant_fixed(tenant_id,charge_type_id,enabled,amount) VALUES(?,?,1,?)", (tid, trash["id"], tr))
        if cat == "Другое":   # склад без охраны — пример индивидуального отключения
            conn.execute("INSERT INTO tenant_fixed(tenant_id,charge_type_id,enabled,amount) VALUES(?,?,0,NULL)", (tid, guard))
        conn.execute("INSERT INTO tenant_meters(tenant_id,charge_type_id,meter_no,initial_reading,enabled) VALUES(?,?,?,?,1)",
                     (tid, el["id"], f"СЕ-{tid:03d}{area}", m0))
        t = A.get_tenant(tid)
        events = [("2026-07-31", 0, lambda: [A.set_tenant_opening(tid, c_, a, date(2026, 7, 31), OPENING[name][2], None, 1)
                                             for c_, a in (("USD", OPENING[name][0]), ("UZS", OPENING[name][1]))] if name in OPENING else None),
                  ("2026-08-01", 0, lambda: A.generate_invoice(t, "2026-08")),
                  ("2026-09-01", 0, lambda: A.generate_invoice(t, "2026-09")),
                  ("2026-09-01", 1, lambda: A.save_reading(t, el, "2026-08", m8, "2026-09-01")),
                  ("2026-10-01", 0, lambda: A.generate_invoice(t, "2026-10")),
                  ("2026-10-01", 1, lambda: A.save_reading(t, el, "2026-09", m9, "2026-10-01"))]
        for d, amt, c, m in pays:   # оплаты — по порядку дат, чтобы разбивка $/сум была как в жизни
            events.append((d, 2, (lambda d=d, amt=amt, c=c, m=m: A.insert_payment(t, d, amt, c, m, "", None, 1))))
        for _, _, fn in sorted(events, key=lambda e: (e[0], e[1])):
            fn()
    conn.commit()
    return len(TENANTS) + create_clients(conn)


CLIENTS = [  # имя, телефон, компания, [(дата, срок, №, описание, вид, сумма)], [(дата, сумма, способ)], пауза до
    ("Азиз Каримов", "+998 90 111 22 33", "Mebel Plus", [
        ("2026-08-20", "2026-08-27", "2481", "Распил 40 листов + кромка 320 м", "services", 480_000),
        ("2026-08-20", "2026-08-27", "2481", "ЛДСП Kronospan белый 16 мм — 40 листов", "materials", 18_400_000),
        ("2026-09-25", "2026-10-02", "2533", "Распил 25 листов, присадка", "services", 310_000)],
     [("2026-09-01", 10_000_000, "transfer")], None),
    ("Нилуфар Каримова", "+998 93 222 33 44", "ИП Каримова Н.", [
        ("2026-09-10", "2026-09-17", "2502", "МДФ 18 мм — 12 листов", "materials", 6_240_000),
        ("2026-09-10", "2026-09-17", "2502", "Распил + кромка ПВХ 2 мм", "services", 260_000)],
     [("2026-09-28", 3_000_000, "cash")], None),
    ("Shoxrux Mebel", "+998 97 333 44 57", "Shoxrux Mebel MChJ", [
        ("2026-10-01", "2026-10-08", "2550", "Распил 60 листов + кромка 500 м", "services", 730_000)],
     [], None),
    ("Рустам Ахмедов", "+998 99 444 55 66", "", [
        ("2026-07-15", "2026-07-22", "2390", "ЛДСП Egger дуб — 15 листов", "materials", 8_250_000)],
     [("2026-08-10", 4_000_000, "cash")], "2026-10-20"),
    ("Бахром Турсунов", "+998 90 777 12 21", "", [], [("2026-09-15", 50, "USD", "cash")], None),
    ("Студия кухонь «Уют»", "+998 95 555 66 77", "Uyut Kitchen", [
        ("2026-09-05", "2026-09-12", "2491", "Распил, кромка, присадка (кухня)", "services", 940_000)],
     [("2026-09-12", 940_000, "card")], None),
]


CLIENT_OPENING = {  # имя: (сум, $, комментарий)
    "Рустам Ахмедов": (1_500_000, 0, "старый долг по тетради"),
    "Бахром Турсунов": (0, 120, "материалы в долг, 2025 г."),
}


UZ_LANG = {"ИП Рахимов Б. (корпусная мебель)", "Shoxrux Mebel", "Азиз Каримов"}


def create_clients(conn):
    now = A.now
    if A.q("SELECT 1 FROM clients WHERE is_demo=1", one=True):
        return 0
    for name, phone, comp, debts, pays, pause in CLIENTS:
        cid = conn.execute("INSERT INTO clients(name,phone,company,notes,active,sms_enabled,sms_paused_until,tg_code,is_demo,created_at) "
                           "VALUES(?,?,?,?,1,1,?,?,1,?)", (name, phone, comp, "Демо-клиент", pause, A.new_code(), now())).lastrowid
        if name in UZ_LANG:
            conn.execute("UPDATE clients SET lang='uz' WHERE id=?", (cid,))
        for d, due, no, desc, kind, amt in debts:
            conn.execute("INSERT INTO client_debts(client_id,doc_date,due_date,order_no,description,kind,amount,created_at,is_demo) "
                         "VALUES(?,?,?,?,?,?,?,?,1)", (cid, d, due, no, desc, kind, amt, now()))
        if name in CLIENT_OPENING:   # старый долг без заказа (в сумах и/или в $)
            zo, uo, note = CLIENT_OPENING[name]
            A.set_client_opening(cid, "UZS", zo, date(2026, 7, 1), note, None, 1)
            A.set_client_opening(cid, "USD", uo, date(2026, 7, 1), note, None, 1)
        for pay in pays:
            d, amt, m = pay[0], pay[1], pay[-1]
            c_ = pay[2] if len(pay) == 4 else "UZS"
            rate = A.fx_rate()
            up, zp = A.alloc_split(*A.client_pockets(cid), amt, c_, rate)
            conn.execute("INSERT INTO client_payments(client_id,pay_date,amount,method,created_at,is_demo,currency,fx_rate,usd_part,uzs_part) "
                         "VALUES(?,?,?,?,?,1,?,?,?,?)", (cid, d, amt, m, now(), c_, rate, up, zp))
    conn.commit()
    return len(CLIENTS)
