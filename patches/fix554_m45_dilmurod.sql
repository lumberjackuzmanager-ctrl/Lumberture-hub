-- 12.11.2026: эгаси — 03.11 №6658 даги 45° (60 м) ни Дилмурод кесган; Жасур ака ва Огабекдан олиб, Дилмуродга
UPDATE raspil_job SET op_id=18, helper_id=NULL, info=COALESCE(info,'')||' | 12.11: 45° Дилмурод кесган (эгаси)' WHERE id=925 AND op_id=4 AND helper_id=5;
DELETE FROM k_kpi WHERE ym='2026-11' AND day=3 AND col_id IN (61,62) AND EXISTS(SELECT 1 FROM raspil_job WHERE id=925 AND op_id=18);
INSERT OR REPLACE INTO k_kpi(ym,day,col_id,qty) SELECT '2026-11',3,55,60 WHERE EXISTS(SELECT 1 FROM raspil_job WHERE id=925 AND op_id=18);
