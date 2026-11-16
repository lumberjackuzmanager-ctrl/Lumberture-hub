-- fix566 (16.11 18:04, эгаси): «Санжар 10-03 қолсин, қолганини тозалаб рўйхатдан яшир»
-- «Санжар ака 10-03» (BJ, код 409): 2 та накладной (№855, №902, 02.2026, $30.39) ва тўлов қатори (№47) → «Санжар 10-03» (BL, код 411)
UPDATE erp_docs SET client='BL' WHERE id IN (6828,6875) AND client='BJ' AND kind='sale';
UPDATE erp_lines SET client='BL' WHERE doc_id=15229 AND client='BJ';
UPDATE erp_clients SET deleted=1, src='pf' WHERE ext='BJ' AND name='Санжар ака 10-03' AND COALESCE(debt1c,0)=0
   AND NOT EXISTS(SELECT 1 FROM erp_docs WHERE client='BJ') AND NOT EXISTS(SELECT 1 FROM erp_lines WHERE client='BJ');
INSERT INTO erp_log(at,user,doc_id,action,detail)
SELECT datetime('now','localtime'),'admin',NULL,'client_mark','BJ: Санжар ака 10-03 — «Санжар 10-03» (BL) билан бирлаштирилди (2 накладной, 1 тўлов қатори; эгаси)'
 WHERE EXISTS(SELECT 1 FROM erp_clients WHERE ext='BJ' AND deleted=1)
   AND NOT EXISTS(SELECT 1 FROM erp_log WHERE detail LIKE 'BJ: Санжар ака 10-03 —%');
