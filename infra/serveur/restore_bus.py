"""Restauration de la base du serveur IA-partag depuis une sauvegarde.
Usage : python restore_bus.py            -> liste les sauvegardes
        python restore_bus.py FICHIER    -> restaure (le serveur doit être arrêté)
La base actuelle n'est jamais écrasée sans copie : elle est conservée en bus.db.avant-restauration-<date>."""
import pathlib, shutil, socket, sqlite3, sys, datetime, json
ROOT = pathlib.Path(__file__).resolve().parent
DB, BK = ROOT / "data" / "bus.db", ROOT / "backups"
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
if len(sys.argv) < 2:
    for f in sorted(BK.glob("bus-*.db")): print(f.name, f.stat().st_size, "octets")
    sys.exit(0)
src = BK / pathlib.Path(sys.argv[1]).name
if not src.exists(): sys.exit(f"sauvegarde introuvable : {src}")
s = socket.socket(); s.settimeout(1)
if s.connect_ex((cfg["host"], cfg["port"])) == 0: sys.exit("Arrête d'abord le serveur (raccourci ON/OFF), puis relance cette commande.")
c = sqlite3.connect(src); ok = c.execute("PRAGMA integrity_check").fetchone()[0]; c.close()
if ok != "ok": sys.exit(f"sauvegarde corrompue : {ok}")
keep = DB.with_name("bus.db.avant-restauration-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
if DB.exists(): shutil.copy2(DB, keep)
for ext in ("-wal", "-shm"):
    p = DB.with_name(DB.name + ext)
    if p.exists(): p.rename(p.with_name(p.name + ".avant-restauration"))
shutil.copy2(src, DB)
print("Restauré depuis", src.name, "| ancienne base conservée :", keep.name)
