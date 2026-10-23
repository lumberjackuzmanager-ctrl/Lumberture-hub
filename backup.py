# -*- coding: utf-8 -*-
"""Резервная копия базы: python backup.py  -> data/backups/rent_ГГГГ-ММ-ДД_ЧЧММ.db (последние 30 копий).
   Безопасно при работающей программе (SQLite backup API).
   Копия вне ПК: если есть синхронизируемая папка Google Диска (Google Drive для компьютера) или OneDrive,
   копия дополнительно кладётся в <папка>/LumbertureHub-backups (там тоже последние 30).
   Папку можно указать явно: переменная RENT_OFFSITE_DIR или первая строка файла data/offsite_dir.txt.
   В облако копируется только база — без сессии Telegram и секретного ключа."""
import os, sqlite3, glob, json, shutil, string
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("RENT_DB", os.path.join(BASE, "data", "rent.db"))
DATA = os.path.dirname(DB)
OUT = os.path.join(DATA, "backups")
KEEP = 30
SUB = "LumbertureHub-backups"


def _signed_in_onedrive(path):
    """OneDrive синхронизирует папку только после входа в аккаунт (в реестре есть UserFolder)."""
    try:
        import winreg
    except ImportError:
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    return False
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        folder = winreg.QueryValueEx(s, "UserFolder")[0]
                        if folder and os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(folder))):
                            return True
                except OSError:
                    continue
    except OSError:
        return False


def find_offsite():
    """-> (папка или '', откуда взята / почему нет)."""
    env = os.environ.get("RENT_OFFSITE_DIR", "").strip()
    if env:
        return env, "RENT_OFFSITE_DIR"
    cfg = os.path.join(DATA, "offsite_dir.txt")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8-sig") as fh:
            p = (fh.readline() or "").strip()
        if p:
            return p, "data/offsite_dir.txt"
    home = os.path.expanduser("~")
    # Google Drive для компьютера: диск G: (или другой) с папкой «My Drive» / «Мой диск»; старый «Автозагрузка и синхронизация» — ~/Google Drive
    cands = []
    if os.name == "nt":
        cands += [f"{d}:\\{n}" for d in string.ascii_uppercase[3:] for n in ("My Drive", "Мой диск")]
    cands += [os.path.join(home, n) for n in ("My Drive", "Мой диск", "Google Drive", "Google Диск")]
    for p in cands:
        if os.path.isdir(p):
            return p, "Google Диск"
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        p = os.environ.get(var, "")
        if p and os.path.isdir(p) and _signed_in_onedrive(p):
            return p, "OneDrive"
    return "", "не найден Google Диск / OneDrive с выполненным входом"


def prune(folder, keep=KEEP):
    for old in sorted(glob.glob(os.path.join(folder, "rent_*.db")))[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass


CODE_KEEP = 14
CODE_PARTS = ("*.py", "*.bat", "*.vbs", "*.txt", "*.md", "templates", "static", "lang", "tests")


def disk_dir():
    """Платформа турган дискда, платформа папкасидан ташқарида: D:\\LumbertureHub_backups (папка ўчса ҳам нусха қолади)."""
    env = os.environ.get("RENT_DISK_BACKUP_DIR", "").strip()
    if env:
        return env
    drive = os.path.splitdrive(BASE)[0]
    return (drive + os.sep if drive else os.path.dirname(BASE) + os.sep) + "LumbertureHub_backups"


def code_zip(target):
    """Код нусхаси (кунига битта): .py, шаблонлар, static, луғатлар, .bat — data (база, сессия, калит) киритилмайди."""
    import zipfile
    zp = os.path.join(target, f"code_{datetime.now():%Y-%m-%d}.zip")
    if os.path.exists(zp):
        return zp
    with zipfile.ZipFile(zp + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        for part in CODE_PARTS:
            for p in glob.glob(os.path.join(BASE, part)):
                if os.path.isdir(p):
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
                        for fn in files:
                            fp = os.path.join(root, fn)
                            z.write(fp, os.path.relpath(fp, BASE))
                elif os.path.isfile(p):
                    z.write(p, os.path.relpath(p, BASE))
    os.replace(zp + ".tmp", zp)
    for old in sorted(glob.glob(os.path.join(target, "code_*.zip")))[:-CODE_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return zp


def main():
    os.makedirs(OUT, exist_ok=True)
    dst_path = os.path.join(OUT, f"rent_{datetime.now():%Y-%m-%d_%H%M}.db")
    src, dst = sqlite3.connect(DB), sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    src.close(); dst.close()
    prune(OUT)
    print("Backup OK:", dst_path, "| integrity:", ok)
    status = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "local": dst_path, "integrity": ok, "offsite_dir": "", "offsite_result": ""}
    root, how = find_offsite()
    if root:
        target = os.path.join(root, SUB)
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(dst_path, os.path.join(target, os.path.basename(dst_path)))
            prune(target)
            status.update(offsite_dir=target, offsite_result=f"OK ({how}), копий: {len(glob.glob(os.path.join(target, 'rent_*.db')))}")
            print("Off-site copy OK:", target)
        except OSError as e:
            status.update(offsite_dir=target, offsite_result=f"ошибка копирования: {e}")
            print("Off-site copy FAILED:", e)
    else:
        status["offsite_result"] = how
        print("Off-site: skipped —", how)
    dd = disk_dir()                                       # платформа турган дискка (папкадан ташқари) — база ва код
    try:
        os.makedirs(dd, exist_ok=True)
        shutil.copy2(dst_path, os.path.join(dd, os.path.basename(dst_path)))
        prune(dd)
        zp = code_zip(dd)
        status.update(disk_dir=dd, disk_result=f"OK, база копий: {len(glob.glob(os.path.join(dd, 'rent_*.db')))}, код: {os.path.basename(zp)}")
        print("Disk copy OK:", dd)
    except OSError as e:
        status.update(disk_dir=dd, disk_result=f"ошибка: {e}")
        print("Disk copy FAILED:", e)
    with open(os.path.join(DATA, "backup_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
