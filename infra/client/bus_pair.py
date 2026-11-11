"""Appairage : récupère le fichier client (url + jeton + agent) à partir d'un code à usage unique généré par l'humain sur le PC 1.
Usage : python bus_pair.py CODE CHEMIN_DU_FICHIER_CLIENT [URL_SERVEUR]
Le jeton n'est jamais affiché ; le fichier n'est jamais écrasé."""
import json, os, pathlib, subprocess, sys, urllib.error, urllib.request
if len(sys.argv) < 3: sys.exit(__doc__)
code, out = sys.argv[1], pathlib.Path(sys.argv[2])
url = sys.argv[3] if len(sys.argv) > 3 else "http://100.83.96.5:8765"
if out.exists(): sys.exit(f"Le fichier {out} existe déjà : je ne l'écrase pas.")
rq = urllib.request.Request(url + "/pair", data=json.dumps({"code": code}).encode(), headers={"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(rq, timeout=10) as r: res = json.loads(r.read().decode())
except urllib.error.HTTPError as e:
    sys.exit(f"Appairage refusé ({e.code}) : {e.read().decode()}")
except Exception as e:
    sys.exit(f"Serveur injoignable : {e}")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({"url": res["url"], "token": res["token"], "agent": res["agent"]}), encoding="utf-8")
if os.name == "nt":   # fichier réservé à l'utilisateur courant
    subprocess.run(["icacls", str(out), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"], capture_output=True)
print(f"OK : fichier client créé pour le Claude {res['agent']} : {out} (jeton non affiché)")
