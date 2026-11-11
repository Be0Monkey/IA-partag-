"""Client MCP du bus IA-partag : donne à Claude les outils envoyer_message / lire_messages / statut.
Configuration : variable d'environnement BUS_CONFIG = chemin d'un JSON {url, token, agent} (secret, hors dépôt)."""
import json, os, urllib.request, urllib.error, uuid, time
from mcp.server.mcpserver import MCPServer

CFG = json.loads(open(os.environ["BUS_CONFIG"], encoding="utf-8-sig").read())
mcp = MCPServer("ia-partag-bus")

def call(path, method="GET", body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": "Bearer " + CFG["token"]}
    if data: h["Content-Type"] = "application/json"
    last = ""
    for i in range(retries):   # reconnexion : on réessaie (l'uid évite les doublons)
        try:
            with urllib.request.urlopen(urllib.request.Request(CFG["url"] + path, data=data, headers=h, method=method), timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"erreur": e.read().decode()}
        except Exception as e:
            last = str(e); time.sleep(1 + i)
    return {"erreur": f"serveur injoignable ({last}). Réessaie plus tard ou préviens l'humain."}

@mcp.tool()
def envoyer_message(a: str, texte: str, priorite: str = "normale", fil: str = "") -> str:
    """Envoie un message à l'autre Claude ('A' ou 'B'). priorite = normale|urgente. fil = identifiant du fil pour répondre dans la même discussion (vide = nouveau fil)."""
    return json.dumps(call("/send", "POST", {"to": a, "text": texte, "priority": priorite, "thread": fil, "uid": uuid.uuid4().hex}), ensure_ascii=False)

@mcp.tool()
def lire_messages() -> str:
    """Lit les nouveaux messages qui te sont destinés (ils sont ensuite marqués comme lus = accusé de réception)."""
    res = call("/inbox")
    if "erreur" in res: return json.dumps(res, ensure_ascii=False)
    if not res["messages"]: return "(aucun nouveau message)"
    return "\n".join(f"[{m['date']}] fil={m['thread']} de={m['de']} priorité={m['priorite']} : {m['texte']}" for m in res["messages"])

@mcp.tool()
def statut() -> str:
    """Indique qui est connecté et combien de messages sont en attente."""
    return json.dumps(call("/status"), ensure_ascii=False)

@mcp.tool()
def demander_validation(action: str, details: str = "") -> str:
    """Demande l'accord de l'HUMAIN avant une action sensible (suppression, argent, sécurité, architecture, installation à risque, fusion avec conflit). N'exécute PAS l'action tant que l'humain n'a pas approuvé : la décision t'arrive dans ta boîte (message de H, fil validation-N). action = une phrase précise ; details = contexte et risques."""
    return json.dumps(call("/approval", "POST", {"action": action, "details": details}), ensure_ascii=False)

@mcp.tool()
def etat_validation(id: int) -> str:
    """Donne l'état d'une demande de validation que tu as faite (en attente, approuvée, refusée, expirée)."""
    return json.dumps(call(f"/approval/{int(id)}"), ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
