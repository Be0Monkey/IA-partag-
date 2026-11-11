"""Surveillant : écoute le serveur ; dès qu'un message attend l'agent, réveille Claude (exit 2 + texte sur stderr)."""
import json, os, sys, time, urllib.request, tempfile, pathlib
cfg = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
duree = int(sys.argv[2]) if len(sys.argv) > 2 else 300
lock = pathlib.Path(tempfile.gettempdir()) / f"iapartag_watcher_{cfg['agent']}.lock"
if lock.exists() and time.time() - lock.stat().st_mtime < duree + 10: sys.exit(0)   # un seul surveillant à la fois
lock.write_text(str(os.getpid()))
def non_lus():
    rq = urllib.request.Request(cfg["url"] + "/inbox?peek=1", headers={"Authorization": "Bearer " + cfg["token"]})
    with urllib.request.urlopen(rq, timeout=10) as r: return json.loads(r.read().decode()).get("non_lus", 0)
fin = time.time() + duree
try:
    while time.time() < fin:
        try: n = non_lus()
        except Exception: n = 0; time.sleep(5)     # coupure réseau : on réessaie sans planter
        if n:
            lock.unlink(missing_ok=True)
            print(f"[RÉVEIL AUTOMATIQUE - hook du projet] {n} nouveau(x) message(s) pour le Claude {cfg['agent']}. Applique le protocole du CLAUDE.md : appelle lire_messages, traite les demandes ordinaires, réponds avec envoyer_message.", file=sys.stderr)
            sys.exit(2)
        time.sleep(3)
finally:
    if lock.exists(): lock.unlink(missing_ok=True)
