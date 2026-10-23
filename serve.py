# -*- coding: utf-8 -*-
"""Запуск в рабочем режиме (Windows и Linux): WSGI-сервер waitress + фоновые задачи
(Telegram-бот, очередь сообщений и SMS, автосчета, напоминания).
   python serve.py   -> http://0.0.0.0:8081   Переменные: RENT_PORT, HOST, THREADS."""
import os, socket, sys
from waitress import serve
from app import app, DB_PATH, start_background_jobs

def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ips.add(s.getsockname()[0]); s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)

def restart_watch():
    """data/restart.flag пайдо бўлса — серверни qayta_ishga_tushirish.bat билан қайта ишга туширади
    (янгиланиш файллари масофадан ўрнатилгандан кейин экранда босмасдан). Фақат Windows."""
    import threading, time, subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    flag = os.path.join(here, "data", "restart.flag")
    bat = os.path.join(here, "qayta_ishga_tushirish.bat")

    def loop():
        while True:
            time.sleep(5)
            try:
                if os.path.exists(flag) and os.name == "nt" and os.path.exists(bat):
                    os.remove(flag)
                    try:
                        import app as _A
                        _A.errlog.warning("restart.flag: server qayta ishga tushirilmoqda")
                    except Exception:
                        pass
                    vbs = os.path.join(here, "restart_hidden.vbs")
                    with open(vbs, "w", encoding="ascii") as f:      # wscript — сервердан мустақил жараён, ойнасиз
                        f.write('Set sh = CreateObject("WScript.Shell")\r\nsh.CurrentDirectory = "%s"\r\n'
                                'sh.Run "cmd /c ""%s""", 0, False\r\n' % (here, bat))
                    try:
                        os.startfile(vbs)                             # Explorer'да икки марта босгандек
                    except Exception:
                        subprocess.Popen(["wscript.exe", "//B", vbs], cwd=here, close_fds=True,
                                         creationflags=0x00000200 | 0x01000000)   # NEW_GROUP | BREAKAWAY_FROM_JOB
            except Exception:
                try:
                    import app as _A
                    _A.errlog.exception("restart.flag")
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True, name="restart_watch").start()


def backup_daily():
    """Ҳар куни 20:00 дан кейин база бэкапи (data/backups + Google Диск). Windows вазифасига боғлиқ эмас."""
    import threading, time, json
    from datetime import datetime

    def last_at():
        try:
            with open(os.path.join(os.path.dirname(DB_PATH), "backup_status.json"), encoding="utf-8") as fh:
                return json.load(fh).get("at") or ""
        except Exception:
            return ""

    def loop():
        time.sleep(120)
        while True:
            try:
                now = datetime.now()
                la = last_at()
                today = now.strftime("%Y-%m-%d")
                due = (not la or (now.hour >= 20 and la < today + " 20:00")       # кечки копия — кун ишидан кейин (тунги копия ҳисобланмайди)
                       or (now - datetime.strptime(la, "%Y-%m-%d %H:%M")).total_seconds() > 36 * 3600)
                if due:
                    import backup
                    backup.DB, backup.DATA = DB_PATH, os.path.dirname(DB_PATH)
                    backup.OUT = os.path.join(backup.DATA, "backups")
                    backup.main()
                    import app as _A
                    _A.errlog.warning("backup: %s", last_at())
            except Exception:
                try:
                    import app as _A
                    _A.errlog.exception("backup daily")
                except Exception:
                    pass
            time.sleep(600)
    threading.Thread(target=loop, daemon=True, name="backup-daily").start()


def install_watchdog(port):
    """Windows вазифаси: ҳар 2 дақиқада watchdog.vbs (сервер ўчиб қолса — ўзи ёқади). Бир марта ўрнатилади."""
    if os.name != "nt":
        return
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    vbs = os.path.join(here, "watchdog.vbs")              # репода бор файл (сервер жавоб бермаса — qayta_ishga_tushirish.bat)
    try:
        if not os.path.exists(vbs):
            return
        q = subprocess.run(["schtasks", "/Query", "/TN", "LumbertureHubWatchdog"], capture_output=True, creationflags=0x08000000)
        if q.returncode != 0:
            r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "2", "/TN", "LumbertureHubWatchdog",
                                "/TR", f'wscript.exe //B "{vbs}"'], capture_output=True, creationflags=0x08000000)
            import app as _A
            _A.errlog.warning("watchdog task: %s %s", r.returncode, (r.stdout or b"").decode("cp866", "replace").strip()[:200])
    except Exception:
        try:
            import app as _A
            _A.errlog.exception("watchdog")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("RENT_PORT", "8081"))
    print("=" * 60)
    print(" Lumberture Hub")
    print(f" DB: {DB_PATH}")
    print(f" Na etom PK:      http://localhost:{port}")
    for ip in local_ips():
        print(f" V lokalnoy seti: http://{ip}:{port}")
    print(" Ne zakryvayte eto okno, poka rabotaet programma (Ctrl+C - ostanovit).")
    print("=" * 60)
    sys.stdout.flush()
    try:
        import app as _A
        _A.errlog.warning("server start %s:%s, IP: %s", host, port, ", ".join(local_ips()) or "-")
    except Exception:
        pass
    start_background_jobs()
    restart_watch()
    install_watchdog(port)
    backup_daily()
    serve(app, host=host, port=port, threads=int(os.environ.get("THREADS", "8")), ident="rent")
