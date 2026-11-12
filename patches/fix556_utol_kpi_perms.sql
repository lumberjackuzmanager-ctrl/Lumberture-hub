-- fix556 (12.11): Утолщенка — Улмас ва Жамшид ака ҳам қилади; Жасур ака ва Огабек двухпилкада ишламайди (45° кесмайди)
-- 1) ботда доступ: Жасур акадан двухпилка олиб ташланади (Улмасда утолщенка бор; Жамшид ака — бошлиқ, ҳаммаси очиқ)
UPDATE ready_workers SET perms='["arra:op", "arra:hp", "pris:hp", "kromka:hp", "oval:hp", "utol:hp", "rover:hp"]'
 WHERE id=7 AND kw_id=4 AND perms='["arra:op", "arra:hp", "dp:hp", "pris:hp", "kromka:hp", "oval:hp", "utol:hp", "rover:hp"]';
-- 2) KPI: утолщенка устуни Улмас ва Жамшид акага (ставка — Дилмуроддаги каби утолщенка ставкаси)
INSERT OR IGNORE INTO k_kpi_cols(worker_id,kind,shift,rate,sort) SELECT 3,'utol','d',(SELECT rate FROM k_kpi_cols WHERE id=54),470;
INSERT OR IGNORE INTO k_kpi_cols(worker_id,kind,shift,rate,sort) SELECT 20,'utol','d',(SELECT rate FROM k_kpi_cols WHERE id=54),480;
INSERT OR IGNORE INTO k_kpi_mcols(ym,col_id,rate,sort) SELECT '2026-11',id,rate,sort FROM k_kpi_cols WHERE kind='utol' AND shift='d' AND worker_id IN (3,20);
-- 3) Жасур ака ва Огабекдаги бўш 45° устунлари (ҳеч қандай қиймат йўқ) — олиб ташланади
DELETE FROM k_kpi_mcols WHERE col_id IN (61,62) AND NOT EXISTS(SELECT 1 FROM k_kpi WHERE col_id IN (61,62)) AND NOT EXISTS(SELECT 1 FROM k_kpi_lock WHERE col_id IN (61,62));
DELETE FROM k_kpi_cols WHERE id IN (61,62) AND kind='m45' AND worker_id IN (4,5) AND NOT EXISTS(SELECT 1 FROM k_kpi WHERE col_id IN (61,62)) AND NOT EXISTS(SELECT 1 FROM k_kpi_mcols WHERE col_id IN (61,62));
