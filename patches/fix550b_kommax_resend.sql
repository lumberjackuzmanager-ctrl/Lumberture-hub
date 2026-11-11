-- 11.11.2026: KOMMAX гуруҳига 03.11–09.11 да кетмай қолган 5 та хабар (3 накладной, 2 тўлов) қайта навбатга — эгаси «Ок»
UPDATE tg_out SET status='pending', error=NULL, attempts=0, next_try=0, chat='KOMMAX & Lumberture', created_at=datetime('now','localtime')
 WHERE id IN (1619,1700,1752,1909,1910) AND status='failed';
