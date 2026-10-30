"""Бош меню (1С'дагидек): Главная · Справочники · Журналы · Отчеты · Операции (27.10: «Документы» йўқ — янги ҳужжат журналдан).
Чекланган роллар (кассир, менежер, базис …): «Рабочее место» + ўзининг тез тугмалари + «Ещё» (қолгани).
Ҳар бир бўлим ичида гуруҳлар (Товары и склад, Аренда, Касса …). Аввалги «Аренда / 1С / Касса» бўлимлари ва
учинчи қатордаги тугмалар шу менюга кўчирилди — ҳар бир саҳифа фақат бир жойда.

Элемент: (номи, endpoint, args, фаол endpoint'лар, ким кўради)
  ким кўради: None — ҳамма (кассадан ташқари), 'adm' — фақат админ. Касса фойдаланувчиси — app.kassa_allowed() бўйича."""

ADM = "adm"

MENU = [
    ("Справочники", [
        ("Товары и склад", [
            ("Каталог платформы", "erp_catalog", {}, ("erp_catalog",), None),
            # 1С товарлари ва Eman/декор/1С склад — фақат админда (1С ҳужжатларини ўқиш учун керак, бошқаларга эмас)
            ("Товары 1С", "erp_goods", {}, ("erp_goods", "erp_good_form"), ADM),
            ("Декоры листов", "erp_decors", {}, ("erp_decors",), ADM),
            ("Каталог Eman", "erp_eman", {}, ("erp_eman",), ADM),
            ("Склад 1С (остатки)", "c1_stock", {}, ("c1_stock",), ADM),
        ]),
        ("Контрагенты", [
            ("Клиенты", "erp_clients", {}, ("erp_clients", "erp_history", "@client_form", "client_card", "client_form"), None),
            ("Поставщики", "erp_suppliers", {}, ("erp_suppliers", "@supplier_form"), None),
            ("Сотрудники", "staff_list", {}, ("staff_list", "staff_card", "staff_form"), None),
        ]),
        ("Аренда", [
            ("Арендаторы", "tenants", {}, ("tenants", "tenant_card", "tenant_form", "statement"), None),
            ("Виды сборов", "charge_types", {}, ("charge_types",), ADM),
            ("Начальные долги", "opening_page", {}, ("opening_page", "opening_import"), ADM),
        ]),
        ("Деньги", [
            ("Счета и виды оплаты", "erp_accounts", {}, ("erp_accounts",), ADM),
            ("Статьи кассы", "kassa_items", {}, ("kassa_items",), ADM),
            ("Партнёры", "kassa_partners", {}, ("kassa_partners",), ADM),
            ("Курс $", "rates_page", {}, ("rates_page",), None),
        ]),
    ]),
    ("Журналы", [
        # 27.10: «Документы» бўлими олиб ташланди — янги ҳужжат журнал тепасидаги «+» тугмасидан (ҳар иш бир жойда)
        ("Продажи и склад", [
            ("Накладные", "erp_docs", {"kind": "sale"}, ("erp_doc", "erp_doc_print", "erp_doc2_print", "erp_sale_edit"), None),
            ("Из Базиса", "erp_basis_list", {}, ("erp_basis_list", "erp_basis"), None),
            ("Поступление денег", "erp_docs", {"kind": "pay"}, ("erp_doc2_edit?kind=pay",), None),
            ("Поступление товара", "erp_docs", {"kind": "receipt"}, ("erp_doc2_edit?kind=receipt",), "receipt"),
            ("Переоценка", "erp_docs", {"kind": "reval"}, ("erp_doc2_edit?kind=reval",), None),
            ("Инвентаризация", "erp_docs", {"kind": "inv"}, ("erp_doc2_edit?kind=inv",), None),
            ("Онлайн-заказы", "erp_online", {}, ("erp_online", "erp_online_view"), None),
            ("3D-проекты с сайта", "erp_online_models", {}, ("erp_online_models",), ADM),
        ]),
        ("Касса", [
            ("Кассовый день", "kassa_day", {}, ("kassa_day",), None),
            ("Деньги у работников", "kassa_wcash", {}, ("kassa_wcash",), None),
            ("Табель и зарплата", "kassa_staff", {}, ("kassa_staff",), None),
            ("Click / Payme", "erp_epay", {}, ("erp_epay",), None),
        ]),
        ("Цех", [
            ("Цех ишлари", "kassa_raspil", {}, ("kassa_raspil", "kassa_raspil_report"), None),
            ("Буюртма тайёр", "ready_page", {}, ("ready_page",), ADM),
            ("Олиб келиш", "erp_pickup", {}, ("erp_pickup",), None),
            ("Логистика", "erp_logist", {}, ("erp_logist",), ADM),
        ]),
        ("Аренда", [
            ("Оплата аренды", "payments", {}, ("payments",), None),
            ("Показания счётчиков", "readings", {}, ("readings",), None),
            ("Счета аренды", "billing", {}, ("billing",), None),
        ]),
        ("Документы и сообщения", [
            ("Документы на бланке", "docs_list", {}, ("docs_list", "docs_card", "docs_form"), None),
            ("Фирменный бланк", "docs_blank", {}, ("docs_blank",), ADM),
            ("Журнал сообщений", "messages", {}, ("messages",), None),
        ]),
    ]),
    ("Отчеты", [
        ("Склад и продажи", [
            ("Остаток товаров", "rep_stock", {}, ("rep_stock",), None),
            ("Отчет по накладным", "rep_sales", {}, ("rep_sales",), None),
            ("Отчет по приходу", "rep_receipts", {}, ("rep_receipts",), None),
            ("Взаиморасчеты", "rep_mutual", {}, ("rep_mutual",), None),
            ("Долги клиентов (1С)", "c1_clients", {}, ("c1_clients", "c1_client", "clients"), None),
        ]),
        ("Аренда", [
            ("Отчёты по аренде", "reports", {}, ("reports",), None),
        ]),
        ("Касса", [
            ("Итоги месяца", "kassa_month", {}, ("kassa_month",), None),
            ("KPI", "kassa_kpi", {}, ("kassa_kpi",), None),
        ]),
    ]),
    ("Операции", [
        ("Связь", [
            ("Связь и контакты", "connections", {}, ("connections",), ADM),
            ("Напоминания должникам", "sms_settings", {}, ("sms_settings",), ADM),
        ]),
        ("Обмен данными", [
            ("Настройка 1С", "c1_settings", {}, ("c1_settings", "onec_page"), ADM),
            ("Импорт кассы из Excel", "kassa_import", {}, ("kassa_import",), ADM),
            ("Ввод остатков из 1С", "erp_stock_import", {}, ("erp_stock_import",), ADM),
            ("Пересчёт склада (цех-бот) и брак", "erp_inv_help", {}, ("erp_inv_help",), ADM),
        ]),
        ("Цех и зарплата", [
            ("Настройки цеха", "raspil_settings", {}, ("raspil_settings",), ADM),
        ]),
        ("Сервис", [
            ("Закрытие периода", "period_lock", {}, ("period_lock",), ADM),
            ("Удаление помеченных объектов", "erp_marked", {}, ("erp_marked",), ADM),
            ("Константы", "settings_page", {}, ("settings_page",), ADM),
            ("Пользователи", "users", {}, ("users",), ADM),
            ("Безопасность", "security_page", {}, ("security_page",), ADM),
            ("Оформление", "appearance", {}, ("appearance",), ADM),
        ]),
    ]),
]


# Чекланган роллар учун тепадаги тез тугмалар (27.10: «функциялар жуда кўп» — ҳар роль ўзининг 5–6 тугмаси,
# қолгани «Ещё» ичида; номлари қисқа — сатр бир қаторга сиғсин). Элемент: (номи, endpoint, args, фаол endpoint'лар)
Q_KASSA = ("Касса", "kassa_day", {}, ("kassa_day",))
Q_SALE = ("Накладные", "erp_docs", {"kind": "sale"}, ("erp_doc", "erp_doc_print", "erp_sale_edit"))
Q_BASIS = ("Из Базиса", "erp_basis_list", {}, ("erp_basis_list", "erp_basis"))
Q_PAY = ("Оплаты", "erp_docs", {"kind": "pay"}, ("erp_doc2_edit?kind=pay",))
Q_REC = ("Приход", "erp_docs", {"kind": "receipt"}, ("erp_doc2_edit?kind=receipt",))
Q_CLI = ("Клиенты", "erp_clients", {}, ("erp_clients", "erp_history", "erp_client_form", "erp_client_card"))
Q_STOCK = ("Остаток", "rep_stock", {}, ("rep_stock",))
Q_CAT = ("Каталог", "erp_catalog", {}, ("erp_catalog",))
Q_DOCS = ("Документы на бланке", "docs_list", {}, ("docs_list", "docs_card", "docs_form"))
Q_SHOP = ("Цех", "kassa_raspil", {}, ("kassa_raspil", "kassa_raspil_report"))
Q_MONTH = ("Итоги месяца", "kassa_month", {}, ("kassa_month",))
Q_TABEL = ("Табель и зарплата", "kassa_staff", {}, ("kassa_staff",))
Q_MUT = ("Взаиморасчеты", "rep_mutual", {}, ("rep_mutual",))
Q_RSALE = ("Отчет по накладным", "rep_sales", {}, ("rep_sales",))
QUICK = {
    "kassa": [Q_KASSA, Q_SALE, Q_PAY, Q_CLI, Q_SHOP],
    "manager": [Q_SALE, Q_BASIS, Q_CLI, Q_STOCK, Q_CAT, Q_DOCS],
    "basis": [Q_BASIS, Q_SALE, Q_CAT, Q_STOCK, Q_CLI],
    "cashier": [Q_KASSA, Q_MONTH, Q_TABEL, Q_MUT, Q_RSALE, Q_CLI],
}
Q_ANY = [Q_KASSA, Q_SALE, Q_BASIS, Q_PAY, Q_REC, Q_CLI, Q_STOCK, Q_CAT, Q_SHOP, Q_MONTH, Q_TABEL, Q_MUT, Q_RSALE, Q_DOCS]
QMAX = 5


def _on(ep, args, e, a, eps):
    """Шу саҳифа элементга тегишлими: endpoint рўйхатда (ёки «endpoint?kind=x»), ёки endpoint ва args мос."""
    for x in eps:
        if "?" in x:
            xe, xq = x.split("?", 1)
            k, v = xq.split("=", 1)
            if ep == xe and str(args.get(k, "")) == v:
                return True
        elif ep == x:
            return True
    return ep == e and all(str(args.get(k, "")) == str(v) for k, v in a.items())


def _doc_kind(e, a):
    return a.get("kind") or ("sale" if e in ("erp_sale_edit", "erp_basis_list") else None)


def _allowed(e, a, kassa_allowed, kinds):
    if not (kassa_allowed and kassa_allowed(e)):
        return False
    k = _doc_kind(e, a)
    return not (kinds is not None and k and e in ("erp_docs", "erp_doc2_edit", "erp_sale_edit") and k not in kinds)


def quick(user, ep, args, kassa_allowed=None, kinds=None):
    """-> [(номи, endpoint, args, фаолми)] — чекланган фойдаланувчининг тез тугмалари (кўпи билан QMAX)."""
    if not user:
        return []
    out = []
    for label, e, a, eps in QUICK.get(user["role"], Q_ANY):
        if len(out) >= QMAX:
            break
        if _allowed(e, a, kassa_allowed, kinds):
            out.append((label, e, a, _on(ep, args, e, a, eps)))
    return out


def build(user, ep, args, sup=False, kassa_allowed=None, kassa_can=None, limited=None, kinds=None, skip=None, merge=None, extra=None):
    """-> [(бўлим, фаолми, [(гуруҳ, [(номи, endpoint, args, фаолми)])])] — фойдаланувчи кўрадиганлари.
    skip — тез тугмаларда бор (endpoint, args) лар (такрорланмасин); merge — ҳамма бўлимлар битта «Ещё» ичида."""
    if not user:
        return []
    adm = user["role"] == "admin"
    konly = (user["role"] == "kassa") if limited is None else limited
    skip = skip or set()
    out = []
    for title, groups in ([("", extra)] if extra else []) + MENU:
        gs, on_sec = [], False
        for gname, items in groups:
            its = []
            for label, e, a, eps, who in items:
                if who == ADM and not adm and gname == "Товары и склад":     # товарлар справочниги: админдан бошқаларга фақат «Каталог платформы»
                    continue
                if konly:
                    if not _allowed(e, a, kassa_allowed, kinds):
                        continue
                    if who == "receipt" and not (kassa_can and kassa_can("kassa_v_receipts")):
                        continue
                elif who == ADM and not adm:
                    continue
                if (e, tuple(sorted(a.items()))) in skip:
                    continue
                act = set(eps)
                if "@client_form" in act and not sup:
                    act.add("erp_client_form")
                if "@supplier_form" in act and sup:
                    act.add("erp_client_form")
                on = _on(ep, args, e, a, act)
                on_sec = on_sec or on
                its.append((label, e, a, on))
            # «Все документы» ва «Реализация» бир вақтда белгиланмасин: аниқроқ (args'ли) элемент фаол бўлса, умумийси ўчади
            if any(on and a for _, _, a, on in its):
                its = [(l, e, a, on and bool(a)) if e == ep else (l, e, a, on) for l, e, a, on in its]
            if its:
                gs.append((gname, its))
        if gs:
            out.append((title, on_sec, gs))
    if merge and out:
        # 30.10 (эгаси): ҳисоботлар — «Ещё» ичида эмас, тепада алоҳида «Отчеты» менюси (кассирга қулай)
        rep = [x for x in out if x[0] == "Отчеты"]
        out = [x for x in out if x[0] != "Отчеты"]
        mg, mon = [], False
        for title, on_sec, gs in out:
            mon = mon or on_sec
            for gname, its in gs:
                ex = next((x for x in mg if x[0] == gname), None)
                if ex:
                    ex[1].extend(its)
                else:
                    mg.append((gname, list(its)))
        out = rep + ([(merge, mon, mg)] if mg else [])
    return out
