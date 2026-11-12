"""Verifie les routes du serveur sur une copie temporaire (jamais la base reelle).
Usage : python routes_check.py <chemin_vers_app.py> <dossier_sortie.json>
"""
import base64, hashlib, hmac, importlib.util, json, pathlib, re, shutil, sys, tempfile

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
root = pathlib.Path(tempfile.mkdtemp(prefix="iapartag-test-"))
(root / "serveur").mkdir(parents=True)
(root / "data").mkdir()
(root / "secrets").mkdir()
shutil.copy(src, root / "serveur" / "app.py")
(root / "config.json").write_text(json.dumps({"host": "127.0.0.1", "port": 8765}), encoding="utf-8")
TOK = {"A": "tok-A-" + "x" * 30, "B": "tok-B-" + "y" * 30, "admin": "adm-" + "z" * 30}
(root / "secrets" / "tokens.json").write_text(json.dumps(TOK), encoding="utf-8")

spec = importlib.util.spec_from_file_location("app_under_test", root / "serveur" / "app.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from starlette.testclient import TestClient
c = TestClient(mod.app, follow_redirects=False)
H = lambda t: {"Authorization": f"Bearer {TOK[t]}"}
BASIC = lambda u, p: {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
OWN = BASIC("humain", TOK["admin"])
def csrf(aid): return hmac.new(TOK["admin"].encode(), f"approval:{aid}".encode(), hashlib.sha256).hexdigest()[:24]

res = {}
def rec(name, r, extra=None):
    res[name] = {"status": r.status_code, **({"extra": extra} if extra is not None else {})}

r = c.post("/send", json={"to": "B", "text": "bonjour B"}, headers=H("A")); rec("send_A_to_B", r)
r = c.post("/send", json={"to": "A", "text": "salut A", "thread": "fil-test"}, headers=H("B")); rec("send_B_to_A", r)
r = c.post("/send", json={"to": "A", "text": ""}, headers=H("B")); rec("send_vide", r)
r = c.post("/send", json={"to": "A", "text": "x"}); rec("send_sans_jeton", r)
r = c.get("/inbox?peek=1", headers=H("B")); rec("inbox_peek_B", r, r.json())
r = c.get("/inbox", headers=H("B")); rec("inbox_B", r, len(r.json().get("messages", [])))
r = c.post("/heartbeat", content=b"test", headers=H("A")); rec("heartbeat_A", r)
r = c.get("/status", headers=H("A")); rec("status_A", r)
r = c.get("/status", headers=OWN); rec("status_owner", r)
r = c.post("/memory/put", json={"key": "projet/x", "value": "valeur 1", "version": 0}, headers=H("A")); rec("mem_put_ok", r, r.json())
r = c.post("/memory/put", json={"key": "projet/x", "value": "valeur 2", "version": 0}, headers=H("A")); rec("mem_put_conflit", r)
r = c.post("/memory/put", json={"key": "projet/y", "value": "password: abc123456789", "version": 0}, headers=H("A")); rec("mem_put_secret", r)
r = c.get("/memory?key=projet/x", headers=H("B")); rec("mem_get_key", r, r.json())
r = c.get("/memory?prefix=proj", headers=H("B")); rec("mem_list", r, r.json())
r = c.post("/approval", json={"action": "Tester la lecture", "details": "rien"}, headers=H("A")); rec("approval_A_local", r, r.json())
aid_a = r.json()["id"]
r = c.post("/approval", json={"action": "Changer l'architecture", "details": "x"}, headers=H("B")); rec("approval_B_critique", r, r.json())
aid_b = r.json()["id"]
r = c.get(f"/approval/{aid_a}", headers=H("A")); rec("approval_get_A", r, r.json())
r = c.get(f"/approval/{aid_a}", headers=H("B")); rec("approval_get_autre", r)
r = c.get("/ui"); rec("ui_sans_auth", r)
r = c.get("/ui", headers=BASIC("humain-A", "faux-mot-de-passe-long")); rec("ui_humainA_mauvais_mdp", r)
r = c.get("/ui", headers=OWN); rec("ui_owner", r, {"contient_Claude_A": "Claude A" in r.text})
r = c.get("/", headers=OWN); rec("racine_owner", r)
r = c.post("/ui/decision", data={"id": aid_a, "csrf": csrf(aid_a), "decision": "approve"}, headers=OWN); rec("decision_owner_A_local", r)
r = c.get(f"/approval/{aid_a}", headers=H("A")); rec("approval_get_apres_decision", r, r.json())
r = c.post("/ui/decision", data={"id": aid_b, "csrf": "mauvais", "decision": "approve"}, headers=OWN); rec("decision_csrf_mauvais", r)
r = c.post("/ui/decision", data={"id": aid_b, "csrf": csrf(aid_b), "decision": "approve"}, headers=OWN); rec("decision_owner_B", r)
r = c.post("/ui/pair-create", data={"agent": "A", "csrf": csrf("pair-A")}); rec("pair_create_sans_auth", r)
r = c.post("/ui/pair-create", data={"agent": "A", "csrf": csrf("pair-A")}, headers=OWN); rec("pair_create_owner", r)
code = re.search(r"<b>([A-Z0-9]{5}-[A-Z0-9]{5})</b>", r.text)
code = code.group(1) if code else None
r = c.post("/pair", json={"code": code}); rec("pair_ok", r, {"agent": r.json().get("agent")})
r = c.post("/pair", json={"code": code}); rec("pair_reutilisation", r)
r = c.post("/ui/invite-create", data={"csrf": csrf("invite")}, headers=OWN); rec("invite_create_owner", r)
icode = re.search(r"<b>([A-Z0-9]{5}-[A-Z0-9]{5})</b>", r.text).group(1)
r = c.get("/invite"); rec("invite_form_get", r)
r = c.post("/invite", data={"code": icode, "pw": "motdepasse-long-1", "pw2": "motdepasse-long-1"}); rec("invite_post_ok", r)
r = c.get("/ui", headers=BASIC("humain-A", "motdepasse-long-1")); rec("ui_humainA_ok", r)
r = c.get("/backup/latest", headers=H("A")); rec("backup_A", r)
r = c.get("/backup/latest", headers=H("B")); rec("backup_B_refuse", r)
r = c.post("/approval", json={"action": "encore"}, headers=H("B")); rec("approval_B_suite", r)

out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v["status"] for k, v in res.items()}, ensure_ascii=False))
