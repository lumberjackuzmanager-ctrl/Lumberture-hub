"""Мижоз гуруҳи номи олдига «@» қўйилса ҳам топилади (28.10: «@KOMMAX & Lumberture.uz» — №6553 юборилмаган)."""
import os, sys, asyncio, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("telethon", types.ModuleType("telethon"))
import tguser as T

class D:
    def __init__(s, name): s.name, s.entity = name, object()
class FC:
    def __init__(s): s.dialogs, s.sent, s.got = [D("Бошқа"), D("KOMMAX & Lumberture.uz")], [], []
    async def is_user_authorized(s): return True
    async def iter_dialogs(s, limit=500):
        for d in s.dialogs: yield d
    async def get_entity(s, x): s.got.append(x); raise ValueError("Cannot find any entity")
    async def send_message(s, ent, text, **k): s.sent.append(ent); return types.SimpleNamespace(id=7)

errs = types.ModuleType("telethon.errors")
for n in ("FloodWaitError", "AuthKeyUnregisteredError", "SessionRevokedError", "UnauthorizedError", "RPCError"):
    setattr(errs, n, type(n, (Exception,), {}))
sys.modules["telethon"].errors = errs; sys.modules["telethon.errors"] = errs
for chat in ("@KOMMAX & Lumberture.uz", "KOMMAX & Lumberture.uz", " @kommax & lumberture.uz "):
    fc = FC()
    r = asyncio.run(T.send_chat_via(fc, chat, "салом"))
    assert r["status"] == "sent" and fc.sent == [fc.dialogs[1].entity] and not fc.got, (chat, r)
fc = FC(); r = asyncio.run(T.send_chat_via(fc, "@lumberture_bot", "салом"))
assert fc.got == ["@lumberture_bot"] and r["status"] == "error", r                 # ҳақиқий @username — get_entity орқали
print("TG GROUP NAME OK")
