"""28.10: ҳамма мижознинг манбаи «Платформа» (src='pf'), 1С'дан келгани src_orig='1c' да эсда — тарих (ҳужжатлар ext бўйича) йўқолмайди."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp as E, online as OL

with A.app.app_context():
    con = A.db()
    assert "src_orig" in {r[1] for r in con.execute("PRAGMA table_info(erp_clients)")}
    con.execute("INSERT INTO erp_clients(ext,code,name,phones,parent,folder,deleted,lc,src) VALUES('AA','12','Алишер','+998 90 123 45 67','',0,0,'алишер','1c')"); con.execute("UPDATE erp_clients SET debt1c=40 WHERE ext='AA'")
    con.execute("INSERT INTO erp_clients(ext,code,name,phones,parent,folder,deleted,lc,src) VALUES('P9','P9','Алишер онлайн','90 123 45 67','',0,0,'алишер','pf')")
    con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,status,src,ret) "
                "VALUES('sale','100',date('now'),'10:00:00','AA',12000,50,600000,0,1,'',0,0,0,'posted','1c',1)")
    con.commit()
    before = E.client_debt(con, "AA")
    con.execute("UPDATE erp_clients SET src_orig=src, src='pf' WHERE src='1c'"); con.commit()      # data/patch_once.sql (fix499)
    assert E.client_debt(con, "AA") == before and before > 0
    assert OL.find_client(con, "+998901234567") == "AA"        # 1С'дан келган эски мижоз онлайн дубликатдан олдин
    h = A.app.test_client()
    with h.session_transaction() as s: s["csrf"] = "t"
    h.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
    t = h.get("/erp/clients?q=Алишер").get_data(as_text=True)
    assert "Алишер" in t and ">1С<" not in t, t[-2000:]
print("CLIENTS SRC OK")
