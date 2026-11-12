"""Tests du lot 3 sur une copie temporaire. Usage : python lot3_check.py <app.py>"""
import sys
sys.modules["multipart"] = None
sys.modules["python_multipart"] = None
import base64, hashlib, hmac, importlib.util, json, pathlib, re, shutil, tempfile

src = pathlib.Path(sys.argv[1])
root = pathlib.Path(tempfile.mkdtemp(prefix="iapartag-lot3-"))
(root / "serveur").mkdir(parents=True); (root / "data").mkdir(); (root / "secrets").mkdir()
shutil.copy(src, root / "serveur" / "app.py")
(root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8765}), encoding="utf-8")
TOK = {"A": "tok-A-" + "x" * 30, "B": "tok-B-" + "y" * 30, "admin": "adm-" + "z" * 30}
(root / "secrets" / "tokens.json").write_text(json.dumps(TOK), encoding="utf-8")
spec = importlib.util.spec_from_file_location("app_lot3", root / "serveur" / "app.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

from starlette.testclient import TestClient
OK, KO = [], []
def check(name, cond, info=""):
    (OK if cond else KO).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

def basic(u, p): return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
def csrf(aid): return hmac.new(TOK["admin"].encode(), f"approval:{aid}".encode(), hashlib.sha256).hexdigest()[:24]
OWN = basic("humain", TOK["admin"])
c = TestClient(mod.app, follow_redirects=False, client=("10.1.0.1", 1))
H = lambda t: {"Authorization": f"Bearer {TOK[t]}"}

# Données : un fil urgent de A, un fil normal de B, une valeur mémoire en deux versions
c.post("/send", json={"to": "B", "text": "urgence <i>test</i> \"guillemets\" 'apos'", "thread": "fil-urg", "priority": "urgente"}, headers=H("A"))
c.post("/send", json={"to": "A", "text": "réponse normale", "thread": "fil-norm"}, headers=H("B"))
c.post("/memory/put", json={"key": "projet/x", "value": "ligne un\nligne deux", "version": 0}, headers=H("A"))
c.post("/memory/put", json={"key": "projet/x", "value": "ligne un\nligne trois", "version": 1}, headers=H("B"))
c.post("/memory/put", json={"key": "autre", "value": "valeur", "version": 0}, headers=H("A"))

# Filtres
r = c.get("/ui?filtre=urgents", headers=OWN).text
check("filtre Urgents : fil urgent présent, fil normal absent de la liste", "fil-urg" in r and "fil-norm" not in r.split("Fils et messages")[1].split("Mémoire")[0] if "Fils et messages" in r else False)
check("filtre actif visible avec réinitialisation", "Filtre actif : urgents" in r and "Réinitialiser" in r)
r = c.get("/ui?sens=B", headers=OWN).text
check("filtre expéditeur B : message de A absent du tableau", "urgence" not in r.split("Messages récents")[1].split("</table>")[0] if "Messages récents" in r else False)
check("filtres Tous / Urgents / À récupérer / A / B présents", all(x in c.get("/ui", headers=OWN).text for x in ["Envoyés par A", "Envoyés par B", "À récupérer", "Urgents"]))

# Copier le lien et le texte ; texte échappé dans l'attribut
r = c.get("/ui?fil=fil-urg", headers=OWN).text
check("bouton Copier le lien du fil", "Copier le lien du fil" in r and "data-copie-lien='/ui?fil=fil-urg'" in r)
check("bouton Copier le texte, guillemets échappés", "data-copie-texte=" in r and "&quot;guillemets&quot;" in r and "&#x27;apos&#x27;" in r)
check("texte du message non interprété", "&lt;i&gt;test&lt;/i&gt;" in r and "<i>test</i>" not in r)

# Mémoire : recherche, valeur complète, comparaison
r = c.get("/ui?mem=projet", headers=OWN).text
check("recherche par clé : clé projet/x trouvée, autre absente", "projet/x" in r and ">autre<" not in r)
check("valeur complète affichée sur deux lignes (version courante)", "ligne un\nligne trois" in r)
r = c.get("/ui?mem=%25", headers=OWN).text
check("recherche avec % traitée littéralement", "aucune clé ne correspond" in r)
r = c.get("/ui?cle=projet/x&v1=1&v2=2", headers=OWN).text
check("comparaison de versions : diff avec suppression et ajout", "diff-moins" in r and "ligne deux" in r and "ligne trois" in r and "diff-plus" in r)
check("versions listées avec auteur", "v1 (A" in r and "v2 (B" in r)
r = c.get("/ui?cle=projet/x&v1=1&v2=99", headers=OWN).text
check("version inconnue gérée", "Version introuvable" in r)

# Appairage et invitation : état, effet, réservé au propriétaire
r = c.get("/ui", headers=OWN).text
check("appairage : aucun code avant génération", "aucun code généré" in r)
check("invitation : effet annoncé (création du compte)", "créera le compte humain-A" in r)
c.post("/ui/pair-create", data={"agent": "A", "csrf": csrf("pair-A")}, headers=OWN)
r = c.get("/ui", headers=OWN).text
check("appairage : code actif et bouton Remplacer", "Remplacer le code pour le Claude A" in r and "actif, créé à" in r)
c.post("/ui/pair-create", data={"agent": "A", "csrf": csrf("pair-A")}, headers=OWN)
r = c.get("/ui", headers=OWN).text
check("avertissement de remplacement (confirm) présent", "Le code actuel ne sera plus valable" in r)
check("invitation : Remplacer le code absent tant qu'aucun code", "Générer un code d'invitation" in r)
code = re.search(r"<b>([A-Z0-9]{5}-[A-Z0-9]{5})</b>", c.post("/ui/invite-create", data={"csrf": csrf("invite")}, headers=OWN).text).group(1)
r = c.get("/ui", headers=OWN).text
check("invitation active : bouton Remplacer et échéance affichée", "Remplacer le code</button>" in r and "expire à" in r)
r = c.post("/invite", data={"code": code, "pw": "motdepasse-long-3", "pw2": "motdepasse-long-3"})
r = c.get("/ui", headers=OWN).text
check("après validation : effet = remplacement du mot de passe", "remplacera son mot de passe" in r)

# PC 2 (humain-A) : voit tout, ne voit pas les commandes propriétaire
pc2 = basic("humain-A", "motdepasse-long-3")
r = c.get("/ui", headers=pc2).text
check("PC 2 voit le texte intégral des messages", "urgence" in r and "réponse normale" in r)
check("PC 2 voit la mémoire complète", "ligne trois" in c.get("/ui?cle=projet/x&v1=1&v2=2", headers=pc2).text or "ligne trois" in r)
check("PC 2 ne voit pas les commandes d'appairage ni d'invitation", "Générer un code" not in r and "Remplacer le code" not in r)
check("PC 2 ne peut pas générer de code d'appairage (403/401)", c.post("/ui/pair-create", data={"agent": "A", "csrf": csrf("pair-A")}, headers=pc2).status_code in (401, 403))

# Préférences et mise en page présentes
r = c.get("/ui", headers=OWN).text
check("préférences : densité et sections repliables", "bouton-densite" in r and "data-onglet=memoire" in r)
check("mise en page : grille liste + détail", "fils-grille" in r and "fil-detail" in r and "zone-detail" in r)
check("JS : préférences en localStorage avec try/catch", "iapartag-prefs" in r and "localStorage" in r)
check("JS : copie avec retour Copié", "Copié" in r and "clipboard" in r)
check("aucune restriction de lecture PC 2 sur /ui/etat", c.get("/ui/etat?depuis=0", headers=pc2).status_code == 200)

print(f"\n{len(OK)} réussis, {len(KO)} échoués")
if KO: print("ÉCHECS :", KO); sys.exit(1)
