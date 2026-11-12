"""Tests du lot 2 sur une copie temporaire. Usage : python lot2_check.py <app.py>"""
import sys
sys.modules["multipart"] = None
sys.modules["python_multipart"] = None
import base64, importlib.util, json, pathlib, shutil, sqlite3, tempfile, datetime

src = pathlib.Path(sys.argv[1])
root = pathlib.Path(tempfile.mkdtemp(prefix="iapartag-lot2-"))
(root / "serveur").mkdir(parents=True); (root / "data").mkdir(); (root / "secrets").mkdir()
shutil.copy(src, root / "serveur" / "app.py")
(root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8765}), encoding="utf-8")
TOK = {"A": "tok-A-" + "x" * 30, "B": "tok-B-" + "y" * 30, "admin": "adm-" + "z" * 30}
(root / "secrets" / "tokens.json").write_text(json.dumps(TOK), encoding="utf-8")
spec = importlib.util.spec_from_file_location("app_lot2", root / "serveur" / "app.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

from starlette.testclient import TestClient
OK, KO = [], []
def check(name, cond, info=""):
    (OK if cond else KO).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

OWN = {"Authorization": "Basic " + base64.b64encode(f"humain:{TOK['admin']}".encode()).decode()}
c = TestClient(mod.app, follow_redirects=False, client=("10.0.0.9", 1))
H = lambda t: {"Authorization": f"Bearer {TOK[t]}"}

# Fil long : 6 messages (dont un de 8000 caractères) puis une tentative refusée
long_txt = ("Bloc de test " * 700)[:7980] + "FIN-DU-TEXTE"
for i in range(6):
    r = c.post("/send", json={"to": "B" if i % 2 == 0 else "A", "text": long_txt if i == 0 else f"message {i} <b>gras</b>", "thread": "fil-long"}, headers=H("A" if i % 2 == 0 else "B"))
    assert r.status_code == 200, (i, r.status_code, r.text)
r = c.post("/send", json={"to": "B", "text": "septième", "thread": "fil-long"}, headers=H("A"))
check("7e message refusé (anti-boucle)", r.status_code == 429, r.status_code)

# Fil court pour l'autre sens
c.post("/send", json={"to": "A", "text": "bonjour", "thread": "fil-court"}, headers=H("B"))

page = c.get("/ui", headers=OWN).text
check("liste des fils : compteur 6/6 et bloqué", "fil-long" in page and "6/6" in page and "bloqué" in page)
check("liste des fils : sens et fil court", "fil-court" in page and "B → A" in page)
check("cartes : dernier contact et à récupérer", "Dernier contact" in page and "Messages à récupérer" in page and "Demandes de validation en attente" in page)
check("compteur demandes sur 5 affiché", "/5" in page)

det = c.get("/ui?fil=fil-long", headers=OWN).text
check("fil ouvert : texte intégral (8000 car. sans coupure)", "FIN-DU-TEXTE" in det)
dd = det[det.index("Fil fil-long :"):]
check("fil ouvert : ordre et 6/6", dd.index("Bloc de test") < dd.index("message 5") and "6/6" in dd)
check("texte échappé dans le fil", "&lt;b&gt;gras&lt;/b&gt;" in det and "<b>gras</b>" not in det)
check("fil inconnu géré", "Fil introuvable" in c.get("/ui?fil=inexistant", headers=OWN).text)

jrn = c.get("/ui?journal=incidents", headers=OWN).text
check("journal incidents : bouton Voir le fil vers le fil bloqué", "Voir le fil" in jrn and "fil=fil-long" in jrn)
check("journal : état actuel du fil, pas un ancien refus présenté comme actif", "toujours bloqué" in jrn)
check("journal : filtre Tout présent", "journal=incidents" in page or "journal=incidents" in jrn)
check("dernière sauvegarde visible", "Dernière sauvegarde réussie" in page)

# Échec de sauvegarde visible
db = sqlite3.connect(mod.DB)
db.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", ((datetime.datetime.now() + datetime.timedelta(seconds=5)).isoformat(timespec="seconds"), "sauvegarde-échec", "disque plein"))
db.commit(); db.close()
check("échec de sauvegarde en bandeau rouge", "Échec de sauvegarde" in c.get("/ui", headers=OWN).text)

# Actualisation : nouveaux messages depuis un repère
base = mod.db().execute("SELECT COALESCE(MAX(id),0) m FROM messages").fetchone()["m"]
c.post("/send", json={"to": "B", "text": "nouveau 1", "thread": "fil-court"}, headers=H("A"))
c.post("/send", json={"to": "B", "text": "nouveau 2", "thread": "fil-court"}, headers=H("A"))
st = c.get(f"/ui/etat?depuis={base}", headers=OWN).json()
check("/ui/etat compte les nouveaux messages depuis le repère", st["nouveaux_messages"] == 2, st)
check("/ui/etat sans auth refusé", c.get("/ui/etat?depuis=0").status_code == 401)

# Présence des libellés de lot 2
page = c.get("/ui", headers=OWN).text
check("bouton Afficher et bandeau nouveautés présents", "iapartagRecharger" in page and "banniere-texte" in page)
check("messages : À récupérer / Récupérée", "À récupérer" in page or "Récupérée" in page)

print(f"\n{len(OK)} réussis, {len(KO)} échoués")
if KO: print("ÉCHECS :", KO); sys.exit(1)
