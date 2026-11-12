"""Tests du lot 1 sur une copie temporaire. Usage : python lot1_check.py <app.py>
Bloque le paquet python-multipart pour prouver que le serveur n'en a pas besoin."""
import sys
sys.modules["multipart"] = None
sys.modules["python_multipart"] = None
import base64, datetime, hashlib, hmac, importlib.util, json, pathlib, re, shutil, sqlite3, tempfile

src = pathlib.Path(sys.argv[1])
root = pathlib.Path(tempfile.mkdtemp(prefix="iapartag-lot1-"))
(root / "serveur").mkdir(parents=True); (root / "data").mkdir(); (root / "secrets").mkdir()
shutil.copy(src, root / "serveur" / "app.py")
(root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8765}), encoding="utf-8")
TOK = {"A": "tok-A-" + "x" * 30, "B": "tok-B-" + "y" * 30, "admin": "adm-" + "z" * 30}
(root / "secrets" / "tokens.json").write_text(json.dumps(TOK), encoding="utf-8")
spec = importlib.util.spec_from_file_location("app_lot1", root / "serveur" / "app.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

from starlette.testclient import TestClient
OK, KO = [], []
def check(name, cond, info=""):
    (OK if cond else KO).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

def basic(u, p): return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
def csrf(aid): return hmac.new(TOK["admin"].encode(), f"approval:{aid}".encode(), hashlib.sha256).hexdigest()[:24]
OWN = basic("humain", TOK["admin"])
X = TestClient(mod.app, follow_redirects=False, client=("10.0.0.5", 1000))   # PC 2 (ou client A)
Y = TestClient(mod.app, follow_redirects=False, client=("10.0.0.6", 1000))   # PC 1
Z = TestClient(mod.app, follow_redirects=False, client=("10.0.0.7", 1000))   # PC 2 après le test de blocage
H = lambda t: {"Authorization": f"Bearer {TOK[t]}"}

# 1. invitation : création du compte humain-A
code = re.search(r"<b>([A-Z0-9]{5}-[A-Z0-9]{5})</b>", Y.post("/ui/invite-create", data={"csrf": csrf("invite")}, headers=OWN).text).group(1)
r = Y.post("/invite", data={"code": code, "pw": "motdepasse-long-1", "pw2": "motdepasse-long-1"})
check("invitation sans python-multipart", r.status_code == 200 and "Accès créé" in r.text, r.text[:200])
PW2 = "motdepasse-long-1"

# 2. blocage par client : 5 échecs depuis X avec humain-A
for i in range(5):
    X.get("/ui", headers=basic("humain-A", "mauvais-mot-de-passe"))
r = X.get("/ui", headers=basic("humain-A", PW2))
check("client bloqué après 5 échecs (même avec bon mot de passe)", r.status_code == 429 and "Connexion bloquée" in r.text, r.status_code)
check("message indique motif et durée", "5 échecs en 10 minutes" in r.text and "Réessaie dans" in r.text)
check("nom saisi échappé dans le message", "<script>" not in X.get("/ui", headers=basic("<script>", "x")).text)
r = Y.get("/ui", headers=OWN)
check("autre client (PC 1, propriétaire) non bloqué", r.status_code == 200, r.status_code)
r = X.get("/ui", headers=OWN)
check("même adresse, autre nom : non bloqué (limite par client+nom)", r.status_code == 200, r.status_code)

# 3. deux humains sur la même demande : une seule décision et une seule notification
ra = Y.post("/approval", json={"action": "Relire le cahier", "details": "rien"}, headers=H("A")).json()
aid = ra["id"]
r = Y.post("/ui/decision", data={"id": aid, "csrf": csrf(aid), "decision": "deny", "note": "non"}, headers=OWN)
check("propriétaire refuse : redirection res=ok", r.status_code == 303 and "res=ok" in r.headers["location"], r.headers.get("location"))
r = Y.post("/ui/decision", data={"id": aid, "csrf": csrf(aid), "decision": "approve"}, headers=basic("humain-A", PW2))
check("second humain : redirection res=deja", r.status_code == 303 and "res=deja" in r.headers["location"], r.headers.get("location"))
page = Y.get(r.headers["location"], headers=basic("humain-A", PW2)).text
check("page affiche la décision déjà enregistrée et le proprio", "déjà traitée" in page and "le propriétaire" in page)
msgs = [m for m in mod.db().execute("SELECT * FROM messages WHERE thread=?", (f"validation-{aid}",)).fetchall()]
check("une seule notification part vers Claude A", len(msgs) == 1, len(msgs))

# 4. demande de B : PC 2 ne peut pas trancher, propriétaire oui
rb = Y.post("/approval", json={"action": "Ajouter une fonction", "details": "rien"}, headers=H("B")).json()
bid = rb["id"]
r = Z.post("/ui/decision", data={"id": bid, "csrf": csrf(bid), "decision": "approve"}, headers=basic("humain-A", PW2))
check("humain-A ne peut pas trancher une demande de B", "res=interdit" in r.headers.get("location", ""), r.headers.get("location"))
page = Z.get("/ui", headers=basic("humain-A", PW2)).text
check("À traiter vide pour PC 2 quand seule une demande de B est en attente", "Aucune décision attendue de toi" in page)
check("pas de bouton Approuver sur la demande de B pour PC 2", f"name=id value={bid}" not in page)
r = Y.post("/ui/decision", data={"id": bid, "csrf": csrf(bid), "decision": "approve"}, headers=OWN)
check("propriétaire tranche la demande de B", "res=ok" in r.headers.get("location", ""))

# 5. demande locale de A : humain-A peut trancher ; critique de A : non
ra2 = Y.post("/approval", json={"action": "Tester une lecture", "details": "rien"}, headers=H("A")).json()
rc = Y.post("/approval", json={"action": "Changer le mot de passe du bus", "details": "x"}, headers=H("A")).json()
check("demande de A sensible classée critique", rc["niveau"] == "critique", rc.get("niveau"))
page = Z.get("/ui", headers=basic("humain-A", PW2)).text
check("À traiter PC 2 : la demande locale de A est listée", "Tester une lecture" in page and "Aucune décision attendue de toi" not in page)
r = Z.post("/ui/decision", data={"id": rc["id"], "csrf": csrf(rc["id"]), "decision": "approve"}, headers=basic("humain-A", PW2))
check("PC 2 ne peut pas trancher la critique de A", "res=interdit" in r.headers.get("location", ""))

# 6. échéance dépassée pendant la saisie : expiration, aucune notification
db = sqlite3.connect(mod.DB)
old = (datetime.datetime.now() - datetime.timedelta(hours=25)).isoformat(timespec="seconds")
db.execute("UPDATE approvals SET created=? WHERE id=?", (old, ra2["id"])); db.commit(); db.close()
r = Y.post("/ui/decision", data={"id": ra2["id"], "csrf": csrf(ra2["id"]), "decision": "approve"}, headers=OWN)
check("décision après échéance : res=expiree", "res=expiree" in r.headers.get("location", ""), r.headers.get("location"))
page = Y.get(r.headers["location"], headers=OWN).text
check("expiration affichée", "a expiré" in page)
n = mod.db().execute("SELECT COUNT(*) n FROM messages WHERE thread=?", (f"validation-{ra2['id']}",)).fetchone()["n"]
check("aucune notification d'approbation après expiration", n == 0, n)

# 7. /ui/etat et en-tête
r = Y.get("/ui/etat", headers=OWN)
check("/ui/etat répond en JSON pour le propriétaire", r.status_code == 200 and "en_attente" in r.json(), r.status_code)
check("/ui/etat refuse sans identifiants", Y.get("/ui/etat").status_code == 401)
page = Y.get("/ui", headers=OWN).text
check("libellés corrigés (propriétaire, Connecté)", "propriétaire" in page and "Connecté" in page and "propri?taire" not in page and "Connect?" not in page)
check("cartes A et B de même classe carte, couleurs A/B", 'class=\'carte A\'' in page and 'class=\'carte B\'' in page and "#7DD3FC" in page or "--A:#7DD3FC" in page)
check("meta refresh supprimé", "http-equiv=refresh" not in page)
check("bouton Pause présent", "bouton-pause" in page)

print(f"\n{len(OK)} réussis, {len(KO)} échoués")
if KO: print("ÉCHECS :", KO); sys.exit(1)
