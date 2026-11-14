-- fix563 (14.11 18:49, эгаси): «Ислом ака 37-37 номерини +998770479810 шу номерга ўзгартир ва хабарномани шу номерга жўнат»
UPDATE erp_clients SET phones='77 047-98-10' WHERE ext='3Y' AND name='Ислом ака 37-37' AND phones='33 470-37-37';
UPDATE clients SET phone='+998 77 047 98 10', tg_status='', tg_user_id=NULL WHERE id=63 AND onec_code='1c77:3Y' AND phone='+998 33 470 37 37';
INSERT INTO erp_log(at,user,doc_id,action,detail)
SELECT datetime('now','localtime'),'admin',NULL,'notif_phone','3Y: хабарлар рақами +998 33 470 37 37 → +998 77 047 98 10 (эгаси)'
 WHERE EXISTS(SELECT 1 FROM clients WHERE id=63 AND phone='+998 77 047 98 10')
   AND NOT EXISTS(SELECT 1 FROM erp_log WHERE action='notif_phone' AND detail LIKE '3Y:%+998 77 047 98 10%');
INSERT INTO tg_out(tenant_id,client_id,kind,phone,name,text,status,created_at,file)
SELECT NULL,63,'manual','998770479810','Ислом ака 37-37',
 'Здравствуйте, Ислом ака 37-37! Напоминаем об оплате за материалы и выполненные услуги. Ваша задолженность на 14.11.2026: $124.43. Lumberture, +998 33 409-60-70',
 'pending',datetime('now','localtime'),''
 WHERE EXISTS(SELECT 1 FROM clients WHERE id=63 AND phone='+998 77 047 98 10')
   AND NOT EXISTS(SELECT 1 FROM tg_out WHERE client_id=63 AND phone='998770479810' AND kind='manual');
