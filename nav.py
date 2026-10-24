"""Бош меню (1С'дагидек): Главная · Справочники · Документы · Журналы · Отчеты · Операции.
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
    ("Документы", [
        ("Склад и продажи", [
            ("Накладная", "erp_sale_edit", {}, (), None),
            ("Из Базиса", "erp_basis_list", {}, ("erp_basis_list", "erp_basis"), None),
            ("Поступление товара", "erp_doc2_edit", {"kind": "receipt"}, (), "receipt"),
            ("Поступление денег", "erp_doc2_edit", {"kind": "pay"}, (), None),
            ("Переоценка товаров", "erp_doc2_edit", {"kind": "reval"}, (), None),
            ("Инвентаризация товаров", "erp_doc2_edit", {"kind": "inv"}, (), None),
        ]),
        ("Аренда", [
            ("Оплата аренды", "payments", {}, ("payments",), None),
            ("Показания счётчиков", "readings", {}, ("readings",), None),
            ("Счета аренды", "billing", {}, ("billing",), None),
        ]),
        ("Документация", [
            ("Документы на бланке", "docs_list", {}, ("docs_list", "docs_card", "docs_form"), None),
            ("Фирменный бланк", "docs_blank", {}, ("docs_blank",), ADM),
        ]),
        ("Касса", [
            ("Кассовый день", "kassa_day", {}, ("kassa_day",), None),
            ("Деньги у работников", "kassa_wcash", {}, ("kassa_wcash",), None),
            ("Табель и зарплата", "kassa_staff", {}, ("kassa_staff",), None),
        ]),
    ]),
    ("Журналы", [
        ("Документы платформы", [
            ("Реализация", "erp_docs", {"kind": "sale"}, ("erp_doc", "erp_doc_print", "erp_doc2_print"), None),
            ("Онлайн-заказы", "erp_online", {}, ("erp_online", "erp_online_view"), None),
            ("Приход", "erp_docs", {"kind": "receipt"}, (), "receipt"),
            ("Касса", "erp_docs", {"kind": "pay"}, (), None),
            ("Инвентаризация", "erp_docs", {"kind": "inv"}, (), None),
        ]),
        ("Оплаты и сообщения", [
            ("Click / Payme", "erp_epay", {}, ("erp_epay",), None),
            ("Журнал сообщений", "messages", {}, ("messages",), None),
        ]),
        ("Цех", [
            ("Цех ишлари", "kassa_raspil", {}, ("kassa_raspil", "kassa_raspil_report"), None),
            ("Буюртма тайёр", "ready_page", {}, ("ready_page",), ADM),
            ("Олиб келиш", "erp_pickup", {}, ("erp_pickup",), None),
            ("Логистика", "erp_logist", {}, ("erp_logist",), ADM),
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
            ("Пересчёт: проданные листы и брак", "erp_inv_help", {}, ("erp_inv_help",), ADM),
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


def build(user, ep, args, sup=False, kassa_allowed=None, kassa_can=None, limited=None, kinds=None):
    """-> [(бўлим, фаолми, [(гуруҳ, [(номи, endpoint, args, фаолми)])])] — фойдаланувчи кўрадиганлари."""
    if not user:
        return []
    adm = user["role"] == "admin"
    konly = (user["role"] == "kassa") if limited is None else limited
    out = []
    for title, groups in MENU:
        gs, on_sec = [], False
        for gname, items in groups:
            its = []
            for label, e, a, eps, who in items:
                if who == ADM and not adm and gname == "Товары и склад":     # товарлар справочниги: админдан бошқаларга фақат «Каталог платформы»
                    continue
                if konly:
                    if not (kassa_allowed and kassa_allowed(e)):
                        continue
                    if who == "receipt" and not (kassa_can and kassa_can("kassa_v_receipts")):
                        continue
                    k = a.get("kind") or ("sale" if e == "erp_sale_edit" else None)
                    if kinds is not None and k and e in ("erp_docs", "erp_doc2_edit", "erp_sale_edit") and k not in kinds:
                        continue
                elif who == ADM and not adm:
                    continue
                act = set(eps)
                if "@client_form" in act and not sup:
                    act.add("erp_client_form")
                if "@supplier_form" in act and sup:
                    act.add("erp_client_form")
                on = ep in act or (ep == e and all(str(args.get(k, "")) == str(v) for k, v in a.items()))
                on_sec = on_sec or on
                its.append((label, e, a, on))
            # «Все документы» ва «Реализация» бир вақтда белгиланмасин: аниқроқ (args'ли) элемент фаол бўлса, умумийси ўчади
            if any(on and a for _, _, a, on in its):
                its = [(l, e, a, on and bool(a)) if e == ep else (l, e, a, on) for l, e, a, on in its]
            if its:
                gs.append((gname, its))
        if gs:
            out.append((title, on_sec, gs))
    return out
