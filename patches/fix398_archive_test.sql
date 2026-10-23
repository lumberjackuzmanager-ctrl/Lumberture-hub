-- fix398 (23.10, эгаси): тест режими — очиқ платформа черновиклари, бот орқали келган тўланмаган ишлар (Ц22 ва ҳ.к.)
-- ва синов онлайн буюртмалари архивга: ҳисобланмайди. Черновиклар «помечен на удаление» — керак бўлса тиклаш мумкин.
UPDATE erp_docs SET status='deleted', hash='', updated_at=datetime('now','localtime')
 WHERE src='pf' AND status='draft' AND (id IN (15544,15641,15642,15643,15644,15657)
   OR (updated_by IN ('цех','онлайн') AND created_at<'2026-10-23 17:35:00'));
INSERT INTO erp_log(at,user,doc_id,action,detail)
 SELECT datetime('now','localtime'),'admin',id,'delete','архив: тест режими (эгаси, 23.10)' FROM erp_docs
 WHERE src='pf' AND status='deleted' AND updated_at>=datetime('now','localtime','-1 minute');
UPDATE raspil_job SET status='cancelled', info='архив: тест режими (эгаси, 23.10)' WHERE status='wait' AND at<'2026-10-23 17:35:00';
UPDATE online_orders SET status='cancel', err='архив: тест режими', upd_by='архив', upd_at=datetime('now','localtime')
 WHERE status IN ('sent','checked') AND created_at<'2026-10-23 17:35:00';
