-- fix559 (12.11 23:24, эгаси): «Азиз 11-11 га бугунги приход денег га 2500 доллар приход қилиб қўй»
-- бугунги «Поступление денег» №309 (id 16304, 12.11) га 5-қатор: Азиз 11-11 (CR) +$2500
INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,cnotes)
SELECT 16304,(SELECT MAX(lineno)+1 FROM erp_lines WHERE doc_id=16304),'','CR',0,'',0,2500,0,0,0,2500,'',''
 WHERE EXISTS(SELECT 1 FROM erp_docs WHERE id=16304 AND kind='pay' AND no='309' AND date='2026-11-12' AND status='posted' AND lock_by IS NULL)
   AND NOT EXISTS(SELECT 1 FROM erp_lines WHERE doc_id=16304 AND client='CR');
UPDATE erp_docs SET usd=ROUND((SELECT SUM(usd) FROM erp_lines WHERE doc_id=16304),2), uzs=ROUND((SELECT SUM(uzs) FROM erp_lines WHERE doc_id=16304),2),
       updated_by='admin', updated_at=datetime('now','localtime')
 WHERE id=16304 AND EXISTS(SELECT 1 FROM erp_lines WHERE doc_id=16304 AND client='CR' AND usd=2500);
INSERT INTO erp_log(at,user,doc_id,action,detail)
SELECT datetime('now','localtime'),'admin',16304,'post','5 қатор (+Азиз 11-11 $2500 — эгаси сўрови)'
 WHERE EXISTS(SELECT 1 FROM erp_lines WHERE doc_id=16304 AND client='CR' AND usd=2500)
   AND NOT EXISTS(SELECT 1 FROM erp_log WHERE doc_id=16304 AND detail LIKE '%Азиз 11-11 $2500%');
