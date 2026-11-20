-- fix572 (20.11 16:20, эгаси): «Абдулбосит 91 770 14 72 қарзи 49.90, Мехриддин (Бекзод) га қўшиб қўй»
-- Янги «Поступление денег» (жами 0, кассага таъсир йўқ): Абдулбосит (GX) +$49.90 — қарзи ёпилади; Мехриддин (Бекзод) (7I) −$49.90 — қарзига қўшилади.
-- Касса ичидаги қарз ўтказишлар шундай ёзилади (мас. №293: D +807.49 / 44 −807.49). Шарт: GX қарзи ҳозир айнан 49.90 бўлса.
INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,status,src,user,created_by,created_at,updated_by,updated_at,ret)
SELECT 'pay',
       (SELECT CAST(MAX(CAST(no AS INTEGER))+1 AS TEXT) FROM erp_docs WHERE kind='pay' AND substr(date,1,4)=strftime('%Y','now','localtime')),
       strftime('%H:%M:%S','now','localtime'), 'GX',
       (SELECT rate FROM erp_docs WHERE src='pf' AND kind IN ('sale','pay') AND rate>0 ORDER BY id DESC LIMIT 1),
       0, 0, 0, 1, 'Қарз ўтказиш: Абдулбосит → Мехриддин (Бекзод) $49.90 (эгаси, 20.11)', 0, 0, 0,
       date('now','localtime'), 'posted', 'pf', 'admin', 'admin', datetime('now','localtime'), 'admin', datetime('now','localtime'), 1
 WHERE NOT EXISTS(SELECT 1 FROM erp_docs WHERE note LIKE 'Қарз ўтказиш: Абдулбосит → Мехриддин%')
   AND ROUND((SELECT COALESCE(debt1c,0) FROM erp_clients WHERE ext='GX')
       + (SELECT COALESCE(SUM(usd-COALESCE(paid,0)),0) FROM erp_docs WHERE src='pf' AND kind='sale' AND status='posted' AND COALESCE(nal,1)=1 AND client='GX')
       - (SELECT COALESCE(SUM(l.debt),0) FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='pay' AND d.status='posted' AND l.client='GX'), 2) = 49.90
   AND EXISTS(SELECT 1 FROM erp_clients WHERE ext='7I' AND name='Мехриддин (Бекзод)' AND deleted=0);
INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,cnotes)
SELECT id,1,'','GX',0,'',0,49.90,0,0,0,49.90,'Мехриддин (Бекзод) га ўтказилди','' FROM erp_docs
 WHERE note LIKE 'Қарз ўтказиш: Абдулбосит → Мехриддин%' AND NOT EXISTS(SELECT 1 FROM erp_lines l WHERE l.doc_id=erp_docs.id);
INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,cnotes)
SELECT id,2,'','7I',0,'',0,-49.90,0,0,0,-49.90,'Абдулбосит қарзи','' FROM erp_docs
 WHERE note LIKE 'Қарз ўтказиш: Абдулбосит → Мехриддин%' AND NOT EXISTS(SELECT 1 FROM erp_lines l WHERE l.doc_id=erp_docs.id AND l.client='7I');
INSERT INTO erp_log(at,user,doc_id,action,detail)
SELECT datetime('now','localtime'),'admin',id,'post','Қарз ўтказиш: Абдулбосит (GX) +$49.90 → Мехриддин (Бекзод) (7I) −$49.90 — эгаси сўрови'
  FROM erp_docs WHERE note LIKE 'Қарз ўтказиш: Абдулбосит → Мехриддин%'
   AND NOT EXISTS(SELECT 1 FROM erp_log WHERE detail LIKE 'Қарз ўтказиш: Абдулбосит (GX)%');
