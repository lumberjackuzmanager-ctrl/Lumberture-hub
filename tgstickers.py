# -*- coding: utf-8 -*-
"""Разовая задача: стикерпак и эмодзи-пак Lumberture (через бот цеха) + эмодзи-статус аккаунта-отправителя.
Запуск: файл data/tg_stickers.json → результат data/tg_stickers_done.json (ссылки t.me/addstickers/…, t.me/addemoji/…)."""
import os, json, time
import requests

import tg

STICKERS = [("a1.webm", "🌲"), ("a10.webm", "👋"), ("a11.webm", "👋"), ("a2.webm", "❤"), ("a6.webm", "❤"), ("a3.webm", "✅"),
            ("a7.webm", "✅"), ("a4.webm", "📦"), ("a8.webm", "📦"), ("a5.webm", "💵"), ("a9.webm", "💵")]
EMOJI = [("e1.webm", "🌲"), ("e2.webm", "🌳")]
OLD_SETS = ("lumberture_by_{b}", "lumberture_emoji_by_{b}")       # аввалги статик паклар — ўчирилади
NEW_SETS = ("lumberture_anim_by_{b}", "lumberture_anim_emoji_by_{b}")
GREEN = (0x3E, 0x64, 0x3C)


def _bot(token, method, files=None, **p):
    data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in p.items()}
    r = requests.post(f"{tg.API_BASE}/bot{token}/{method}", data=data, files=files, timeout=60)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"{method}: {j.get('description')}")
    return j["result"]


def _make_set(token, user_id, name, title, items, folder, kind):
    try:
        st = _bot(token, "getStickerSet", name=name)
        return st                                            # аввал яратилган — қайта яратмаймиз
    except RuntimeError:
        pass
    files, stickers = {}, []
    for i, (fn, emo) in enumerate(items):
        k = f"f{i}"
        files[k] = (fn, open(os.path.join(folder, fn), "rb"), "video/webm" if fn.endswith(".webm") else "image/png")
        stickers.append({"sticker": f"attach://{k}", "format": "video" if fn.endswith(".webm") else "static", "emoji_list": [emo]})
    try:
        _bot(token, "createNewStickerSet", files=files, user_id=user_id, name=name, title=title, stickers=stickers, sticker_type=kind)
    finally:
        for f in files.values():
            f[1].close()
    time.sleep(2)
    return _bot(token, "getStickerSet", name=name)


def run(A):
    p = os.path.join(A.DATA_DIR, "tg_stickers.json")
    if not os.path.isfile(p) or not A.tgu_ready():
        return
    os.replace(p, p + ".work")
    res = {}
    try:
        try:
            job = json.load(open(p + ".work", encoding="utf-8")) or {}
        except (OSError, ValueError):
            job = {}
        token = (A.get_setting("raspil_bot_token", "") or "").strip()
        if not token:
            raise RuntimeError("бот токени йўқ")
        me_bot = _bot(token, "getMe")["username"]
        me = json.loads(A.get_setting("tgu_me", "{}") or "{}")
        uid = int(me["id"])
        api_id, api_hash = A.tgu_cfg()
        if isinstance(job, dict) and job.get("add"):              # мавжуд пакка янги стикерлар (эгаси, 24.10): {"add": [[файл, эмодзи], …]}
            sn = NEW_SETS[0].format(b=me_bot)
            have = len(_bot(token, "getStickerSet", name=sn)["stickers"])
            folder = os.path.join(A.DATA_DIR, "stickers")
            done, errs = [], []
            for fn, emo in job["add"]:
                try:
                    with open(os.path.join(folder, fn), "rb") as fh:
                        _bot(token, "addStickerToSet", files={"f0": (fn, fh, "video/webm")}, user_id=uid, name=sn,
                             sticker={"sticker": "attach://f0", "format": "video", "emoji_list": [emo]})
                    done.append(fn)
                except Exception as e:
                    errs.append(f"{fn}: {e}"[:150])
                time.sleep(1)
            res.update(added=done, errors=errs, before=have, stickers=f"https://t.me/addstickers/{sn}")
            A.errlog.warning("tg stickers add: %s", res)
            raise StopIteration

        async def start(c):                                  # бот аккаунтга мурожаат қила олиши учун
            await c.send_message(me_bot, "/start")
        A.tgu_sender().call(api_id, api_hash, start, timeout=60)
        time.sleep(3)
        folder = os.path.join(A.DATA_DIR, "stickers")
        sn, en = (x.format(b=me_bot) for x in NEW_SETS)
        _make_set(token, uid, sn, "Lumberture", STICKERS, folder, "regular")
        es = _make_set(token, uid, en, "Lumberture", EMOJI, folder, "custom_emoji")
        doc = int(es["stickers"][0]["custom_emoji_id"])

        async def style(c):
            from telethon import functions, types
            out = {}
            await c(functions.account.UpdateEmojiStatusRequest(emoji_status=types.EmojiStatus(document_id=doc)))
            out["status"] = True
            try:                                                 # эмодзи-пакни аккаунтга ўрнатамиз (фон эмодзи учун керак)
                await c(functions.messages.InstallStickerSetRequest(stickerset=types.InputStickerSetShortName(short_name=en), archived=False))
            except Exception as e:
                out["install"] = str(e)[:100]

            async def set_color(for_profile, cid):
                from telethon import errors
                for emo in (doc, None):                          # фон эмодзи қабул қилинмаса — фақат ранг
                    try:
                        await c(functions.account.UpdateColorRequest(for_profile=for_profile,
                                                                     color=types.PeerColor(color=cid, background_emoji_id=emo)))
                        return emo is not None
                    except errors.RPCError as e:
                        out[f"emoji_bg_{'p' if for_profile else 'n'}"] = str(e)[:80]
            try:                                                 # профиль ранги: бренд яшилига энг яқини + фон эмодзи
                pc = await c(functions.help.GetPeerProfileColorsRequest(hash=0))
                best, bd = None, 1e9
                for col in getattr(pc, "colors", []) or []:
                    cs = getattr(col, "colors", None)
                    arr = (getattr(cs, "bg_colors", None) or getattr(cs, "palette_colors", None) or []) if cs else []
                    if not arr or getattr(col, "hidden", False):
                        continue
                    v = arr[0]; rgb = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
                    dd = sum((a - b) ** 2 for a, b in zip(rgb, GREEN))
                    if dd < bd:
                        best, bd = col.color_id, dd
                if best is not None:
                    await set_color(True, best)
                    out["profile_color"] = best
                nc = await c(functions.help.GetPeerColorsRequest(hash=0))
                best, bd = None, 1e9
                for col in getattr(nc, "colors", []) or []:
                    cs = getattr(col, "colors", None); arr = getattr(cs, "colors", None) or [] if cs else []
                    if not arr or getattr(col, "hidden", False):
                        continue
                    v = arr[0]; rgb = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
                    dd = sum((a - b) ** 2 for a, b in zip(rgb, GREEN))
                    if dd < bd:
                        best, bd = col.color_id, dd
                if best is not None:
                    await set_color(False, best)
                    out["name_color"] = best
            except Exception as e:
                out["color_error"] = f"{e.__class__.__name__}: {e}"[:200]
            return out
        res.update(A.tgu_sender().call(api_id, api_hash, style, timeout=90) or {})
        for old in OLD_SETS:                                     # эски статик паклар
            try:
                _bot(token, "deleteStickerSet", name=old.format(b=me_bot)); res.setdefault("deleted", []).append(old.format(b=me_bot))
            except RuntimeError:
                pass
        res.update(stickers=f"https://t.me/addstickers/{sn}", emoji=f"https://t.me/addemoji/{en}")
        A.errlog.warning("tg stickers: %s", res)
    except StopIteration:
        pass
    except Exception as e:
        res["error"] = f"{e.__class__.__name__}: {e}"[:400]
        A.errlog.exception("tg stickers")
    with open(os.path.join(A.DATA_DIR, "tg_stickers_done.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(result=res, at=A.now()), fh, ensure_ascii=False)
    try:
        os.remove(p + ".work")
    except OSError:
        pass
