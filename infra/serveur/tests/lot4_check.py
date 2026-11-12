"""Tests de la version 2 de /ui : liens utiles, onglets, couleurs, zones remplaçables, « Voir plus ».
Usage : python lot4_check.py <app.py>"""
import sys
sys.modules["multipart"] = None
sys.modules["python_multipart"] = None
import base64, importlib.util, json, pathlib, shutil, tempfile

src = pathlib.Path(sys.argv[1])
root = pathlib.Path(tempfile.mkdtemp(prefix="iapartag-lot4-"))
(root / "serveur").mkdir(parents=True); (root / "data").mkdir(); (root / "secrets").mkdir()
shutil.copy(src, root / "serveur" / "app.py")
(root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8765}), encoding="utf-8")
TOK = {"A": "tok-A-" + "x" * 30, "B": "tok-B-" + "y" * 30, "admin": "adm-" + "z" * 30}
(root / "secrets" / "tokens.json").write_text(json.dumps(TOK), encoding="utf-8")
spec = importlib.util.spec_from_file_location("app_lot4", root / "serveur" / "app.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

from starlette.testclient import TestClient
OK, KO = [], []
def check(name, cond, info=""):
    (OK if cond else KO).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

def basic(u, p): return {"Authorization": "Basic " + __import__("base64").b64encode(f"{u}:{p}".encode()).decode()}
OWN = basic("humain", TOK["admin"])
c = TestClient(mod.app, follow_redirects=False, client=("10.2.0.1", 1))
H = lambda t: {"Authorization": f"Bearer {TOK[t]}"}

# Données : un message A→B, un B→A, un message de validation (H), 22 fils, 12 messages
c.post("/send", json={"to": "B", "text": "A vers B", "thread": "f-ab"}, headers=H("A"))
c.post("/send", json={"to": "A", "text": "B vers A", "thread": "f-ab"}, headers=H("B"))
for i in range(11):
    c.post("/send", json={"to": "B", "text": f"fil n {i}", "thread": f"f-{i:02d}"}, headers=H("A"))
    c.post("/send", json={"to": "A", "text": f"fil m {i}", "thread": f"g-{i:02d}"}, headers=H("B"))
r = c.post("/approval", json={"action": "Relire le cahier", "details": "x"}, headers=H("A")).json()
ap = r["id"]
r = c.post("/ui/decision", data={"id": ap}, headers=OWN)  # sans jeton : refusé (403), pas de notification

page = c.get("/ui", headers=OWN).text

# Liens utiles
check("liens utiles : dépôt et pull requests, nouvelle fenêtre",
      "https://github.com/Be0Monkey/IA-partag-" in page and "https://github.com/Be0Monkey/IA-partag-/pulls" in page and 'target=_blank rel=noopener' in page)

# Onglets
check("onglets présents pour le propriétaire", all(f'data-onglet={o}' in page for o in ("fils", "memoire", "journal", "admin")))
pc2 = basic("humain-A", "pas-le-bon-mot-de-passe")
check("PC 2 sans compte valide : accès refusé", c.get("/ui", headers=pc2).status_code == 401)
check("panneau d'administration présent pour le propriétaire", "data-panneau=admin" in page and "zone-admin" in page)

# Zones remplaçables et défilement conservé (JS)
check("thread : lien remplaçable sans rechargement", "data-remplace='zone-fils'" in page)
check("JS : remplacement de zone et history.replaceState", "chargerZone" in page and "history.replaceState" in page)
check("JS : formulaires de recherche remplaçables", "form.hasAttribute('data-remplace')" in page and "data-focus=mem" in page)

# Couleurs des messages
detail = c.get("/ui?fil=f-ab", headers=OWN).text
check("message A→B : classe c-AB dans le détail", "class='bloc c-AB'" in detail)
check("message B→A : classe c-BA dans le détail", "class='bloc c-BA'" in detail)
check("CSS : moitié gauche / droite selon l'expéditeur", "linear-gradient(90deg,var(--A) 50%,var(--B) 50%)" in page and "linear-gradient(90deg,var(--B) 50%,var(--A) 50%)" in page)
check("liste des fils sans couleur (choix validé)", "c-AB" not in page.split('id=liste-fils')[1].split('</table>')[0] if 'id=liste-fils' in page else False)

# « Voir plus »
check("fils : au plus 20 affichés, lien Voir plus", "Voir plus de fils" in page and "nfils=40" in page)
page40 = c.get("/ui?nfils=40", headers=OWN).text
check("fils : nfils=40 affiche les fils restants", "g-10" in page40 and "Voir plus de fils" not in page40.split("id=zone-detail")[0])
check("messages : au plus 10 affichés, lien Voir plus", "Voir plus de messages" in page and "nmsg=20" in page)
check("messages : nmsg=30 supprime le lien", "Voir plus de messages" not in c.get("/ui?nmsg=30", headers=OWN).text)

# Journal : liens remplaçables
check("journal : filtres remplaçables", "data-remplace='zone-journal'" in c.get("/ui?journal=incidents", headers=OWN).text)

# Décision sans jeton : aucune notification
n = mod.db().execute("SELECT COUNT(*) n FROM messages WHERE thread=?", (f"validation-{ap}",)).fetchone()["n"]
check("décision sans jeton : aucune notification", n == 0, n)

print(f"\n{len(OK)} réussis, {len(KO)} échoués")
if KO: print("ÉCHECS :", KO); sys.exit(1)
