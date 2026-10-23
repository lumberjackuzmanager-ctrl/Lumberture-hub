-- fix411 (23.10 23:55, эгаси): платформа «Реализация»даги ҳамма черновиклар (тест онлайн буюртмалари №3–12) — «помечен на удаление».
-- Тиклаш мумкин. Уларга боғланган онлайн буюртмалар — «отменён» (мижозга хабар кетмайди).
UPDATE online_orders SET status='cancel', err='архив: тест режими', upd_by='архив', upd_at=datetime('now','localtime')
 WHERE status IN ('sent','checked','paid') AND doc_id IN (SELECT id FROM erp_docs WHERE src='pf' AND kind='sale' AND status='draft');
UPDATE erp_docs SET status='deleted', hash='', updated_at=datetime('now','localtime') WHERE src='pf' AND kind='sale' AND status='draft';
INSERT INTO erp_log(at,user,doc_id,action,detail)
 SELECT datetime('now','localtime'),'admin',id,'delete','черновиклар ўчирилди (эгаси, 23.10 23:55)' FROM erp_docs
 WHERE src='pf' AND status='deleted' AND updated_at>=datetime('now','localtime','-1 minute');
