"""tgstickers: мавжуд стикерпакка янги стикерлар қўшиш ({"add": [[файл, эмодзи]]}) — эмодзи-статус ва бошқа қадамларсиз."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, tgstickers as T
calls = []
def fake(token, method, files=None, **p):
    calls.append((method, p, sorted((files or {}).keys())))
    return {"username": "lb_bot"} if method == "getMe" else {"stickers": [1, 2, 3]} if method == "getStickerSet" else True
T._bot = fake
T.time.sleep = lambda s: None
with A.app.app_context():
    A.set_settings(raspil_bot_token="1:" + "A" * 30, tgu_me=json.dumps({"id": 777}))
    A.tgu_ready = lambda: True
    A.tgu_cfg = lambda: (1, "h")
    os.makedirs(os.path.join(A.DATA_DIR, "stickers"), exist_ok=True)
    for f in ("b1.webm", "b2.webm"):
        open(os.path.join(A.DATA_DIR, "stickers", f), "wb").write(b"x")
    json.dump({"add": [["b1.webm", "🙏"], ["b2.webm", "🙏"], ["nofile.webm", "🙏"]]}, open(os.path.join(A.DATA_DIR, "tg_stickers.json"), "w"))
    T.run(A)
    adds = [c for c in calls if c[0] == "addStickerToSet"]
    assert len(adds) == 2 and adds[0][1]["name"] == "lumberture_anim_by_lb_bot" and adds[0][1]["user_id"] == 777 and adds[0][2] == ["f0"], adds
    assert adds[0][1]["sticker"]["emoji_list"] == ["🙏"] and adds[0][1]["sticker"]["format"] == "video"
    assert not any(c[0] in ("createNewStickerSet", "deleteStickerSet") for c in calls)
    r = json.load(open(os.path.join(A.DATA_DIR, "tg_stickers_done.json")))["result"]
    assert r["added"] == ["b1.webm", "b2.webm"] and len(r["errors"]) == 1 and r["before"] == 3, r
    assert not os.path.exists(os.path.join(A.DATA_DIR, "tg_stickers.json.work"))
print("STICKERS ADD OK")
