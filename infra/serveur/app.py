import re
"""Serveur central IA-partag (phase 0) : messages, présence, auth par jeton, interface de suivi minimale."""
import base64, datetime, hashlib, hmac, html, json, logging, pathlib, secrets, sqlite3, sys
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "bus.db"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
TOKENS = json.loads((ROOT / "secrets" / "tokens.json").read_text(encoding="utf-8-sig"))
AGENTS = ("A", "B")
MAX_PER_THREAD = 6      # anti-boucle : messages max par fil
MAX_PER_MIN = 20        # limite par agent et par minute
MAX_BODY = 8000         # taille max d'un message
ONLINE_S = 90           # un agent est "connecté" si vu il y a moins de 90 s

logging.basicConfig(filename=ROOT / "data" / "server.log", level=logging.INFO,
                    format="%(asctime)s %(message)s", encoding="utf-8")
now = lambda: datetime.datetime.now().isoformat(timespec="seconds")

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=5000")
    return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, thread TEXT,
            sender TEXT, recipient TEXT, priority TEXT, body TEXT, created TEXT, delivered TEXT);
        CREATE TABLE IF NOT EXISTS agents(name TEXT PRIMARY KEY, last_seen TEXT, info TEXT);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT, action TEXT, details TEXT,
            status TEXT, created TEXT, decided TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS pairings(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, code_hash TEXT, created TEXT, expires TEXT, used INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, detail TEXT);""")

def event(kind, detail, c=None):
    if c is not None: c.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (now(), kind, detail))
    else:
        with db() as c2: c2.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (now(), kind, detail))
    logging.info("%s %s", kind, detail)

def agent_of(req):
    h = req.headers.get("authorization", "")
    if h.startswith("Bearer "):
        for name in AGENTS:
            if hmac.compare_digest(h[7:], TOKENS[name]): return name
    return None

def pw_hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200_000).hex()

HUM_MAX_FAILS = 5   # echecs max d'identifiants sur /ui par fenetre de 10 min, tous clients confondus

def human_role(req):
    """Verifie les identifiants humains, avec blocage apres trop d'echecs."""
    h = req.headers.get("authorization", "")
    if not h.startswith("Basic "): return None
    lim = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat(timespec="seconds")
    with db() as c:
        if c.execute("SELECT COUNT(*) n FROM events WHERE kind='humain-\u00e9chec' AND ts>=?", (lim,)).fetchone()["n"] >= HUM_MAX_FAILS:
            return None
    r = _human_role_check(h)
    if r is None: event("humain-\u00e9chec", "identifiants invalides sur le tableau de bord")
    return r

def _human_role_check(h):
    """'owner' (identifiant humain), 'humain-A' (humain du PC 2) ou None."""
    try: user, pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
    except Exception: return None
    if user == "humain" and hmac.compare_digest(pw, TOKENS["admin"]): return "owner"
    if user == "humain-A":
        with db() as c: r = c.execute("SELECT * FROM humans WHERE name='humain-A'").fetchone()
        if r and hmac.compare_digest(pw_hash(pw, bytes.fromhex(r["salt"])), r["hash"]): return "humain-A"
    return None

def is_admin(req):          # réservé au propriétaire
    return human_role(req) == "owner"

def touch(c, name, info=""):
    c.execute("INSERT INTO agents(name,last_seen,info) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, info=excluded.info", (name, now(), info))

async def send(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    try: d = await req.json()
    except Exception: return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
    to, text, prio = d.get("to"), str(d.get("text", "")), d.get("priority", "normale")
    uid, thread = d.get("uid") or "", d.get("thread") or ""
    if to not in AGENTS or to == me: return JSONResponse({"erreur": "destinataire invalide"}, status_code=400)
    if prio not in ("normale", "urgente"): return JSONResponse({"erreur": "priorité invalide"}, status_code=400)
    if not text.strip() or len(text) > MAX_BODY: return JSONResponse({"erreur": "texte vide ou trop long"}, status_code=400)
    with db() as c:
        touch(c, me)
        if uid:  # idempotence : même uid => pas de doublon
            old = c.execute("SELECT id, thread FROM messages WHERE uid=?", (uid,)).fetchone()
            if old: return JSONResponse({"ok": True, "id": old["id"], "thread": old["thread"], "doublon": True})
        recent = c.execute("SELECT COUNT(*) n FROM messages WHERE sender=? AND created>=?",
                           (me, (datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat(timespec="seconds"))).fetchone()["n"]
        if recent >= MAX_PER_MIN:
            event("limite", f"{me} dépasse {MAX_PER_MIN} msg/min", c)
            return JSONResponse({"erreur": f"limite {MAX_PER_MIN} messages/minute atteinte"}, status_code=429)
        thread = thread or datetime.datetime.now().strftime("%H%M%S") + "-" + (uid[:4] or "x")
        if c.execute("SELECT COUNT(*) n FROM messages WHERE thread=?", (thread,)).fetchone()["n"] >= MAX_PER_THREAD:
            event("anti-boucle", f"fil {thread} bloqué à {MAX_PER_THREAD} messages", c)
            return JSONResponse({"erreur": f"anti-boucle : le fil {thread} a atteint {MAX_PER_THREAD} messages, demandez à l'humain"}, status_code=429)
        cur = c.execute("INSERT INTO messages(uid,thread,sender,recipient,priority,body,created) VALUES(?,?,?,?,?,?,?)",
                        (uid or None, thread, me, to, prio, text, now()))
    logging.info("msg %s->%s fil=%s", me, to, thread)
    return JSONResponse({"ok": True, "id": cur.lastrowid, "thread": thread})

async def inbox(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    peek = req.query_params.get("peek") == "1"
    with db() as c:
        touch(c, me)
        rows = c.execute("SELECT * FROM messages WHERE recipient=? AND delivered IS NULL ORDER BY id", (me,)).fetchall()
        if peek: return JSONResponse({"non_lus": len(rows)})
        if rows: c.execute(f"UPDATE messages SET delivered=? WHERE id IN ({','.join('?'*len(rows))})", (now(), *[r['id'] for r in rows]))
    return JSONResponse({"messages": [{"id": r["id"], "thread": r["thread"], "de": r["sender"], "priorite": r["priority"], "date": r["created"], "texte": r["body"]} for r in rows]})

async def heartbeat(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    with db() as c: touch(c, me, (await req.body())[:200].decode("utf-8", "ignore"))
    return JSONResponse({"ok": True})

def state():
    with db() as c:
        ags = {r["name"]: r for r in c.execute("SELECT * FROM agents")}
        msgs = c.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 30").fetchall()
        evs = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 15").fetchall()
        unread = c.execute("SELECT recipient r, COUNT(*) n FROM messages WHERE delivered IS NULL GROUP BY recipient").fetchall()
    out = {}
    for a in AGENTS:
        r = ags.get(a); ok = False
        if r: ok = (datetime.datetime.now() - datetime.datetime.fromisoformat(r["last_seen"])).total_seconds() < ONLINE_S
        out[a] = {"connecte": ok, "vu": r["last_seen"] if r else None}
    return out, msgs, evs, {u["r"]: u["n"] for u in unread}

async def status(req: Request):
    if not (agent_of(req) or human_role(req)): return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    ags, _, _, unread = state()
    return JSONResponse({"agents": ags, "non_lus": unread})

async def ui(req: Request):
    role = human_role(req)
    if not role:
        return Response("Authentification requise", status_code=401, headers={"WWW-Authenticate": 'Basic realm="IA-partag"'})
    ags, msgs, evs, unread = state(); e = html.escape
    rows = "".join(f"<tr><td>{e(m['created'])}</td><td>{e(m['sender'])}→{e(m['recipient'])}</td><td>{e(m['thread'])}</td><td>{'lu' if m['delivered'] else 'NON LU'}</td><td>{e(m['priority'])}</td><td>{e(m['body'][:200])}</td></tr>" for m in msgs)
    erows = "".join(f"<tr><td>{e(x['ts'])}</td><td>{e(x['kind'])}</td><td>{e(x['detail'])}</td></tr>" for x in evs) or "<tr><td colspan=3>aucune</td></tr>"
    cards = "".join(f"<div class=c><b>Claude {a}</b><br>{'🟢 connecté' if v['connecte'] else '🔴 hors ligne'}<br><small>vu : {e(v['vu'] or 'jamais')}<br>non lus : {unread.get(a, 0)}</small></div>" for a, v in ags.items())
    who = "propri?taire" if role == "owner" else "humain du PC 2 (humain-A)"
    appr = f"<p>Connect? : <b>{who}</b></p>" + approvals_block(role) + memory_block() + ((pairing_block() + invite_block()) if role == "owner" else "")
    page = f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5><title>IA-partag</title>
<style>body{{font:14px system-ui;margin:20px;background:#111;color:#eee}}.c{{display:inline-block;border:1px solid #444;border-radius:8px;padding:12px;margin-right:12px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}td,th{{border-bottom:1px solid #333;padding:4px 8px;text-align:left}}</style>
<h2>IA-partag — suivi</h2>{cards}{appr}<h3>Messages (30 derniers)</h3><table><tr><th>date<th>sens<th>fil<th>état<th>prio<th>texte</tr>{rows}</table>
<h3>Erreurs / garde-fous</h3><table>{erows}</table><small>Rafraîchi toutes les 5 s — {now()}</small>"""
    return HTMLResponse(page)

APPROVAL_TTL_H = 24      # une demande non traitée expire après 24 h
MAX_PENDING = 5          # demandes en attente max par agent

def csrf(aid):
    return hmac.new(TOKENS["admin"].encode(), f"approval:{aid}".encode(), hashlib.sha256).hexdigest()[:24]

def expire(c):
    lim = (datetime.datetime.now() - datetime.timedelta(hours=APPROVAL_TTL_H)).isoformat(timespec="seconds")
    c.execute("UPDATE approvals SET status='expirée', decided=?, note='sans réponse sous 24 h' WHERE status='en attente' AND created<?", (now(), lim))

CRITICAL_RE = re.compile(r"architecture|s[ée]curit[ée]|mot de passe|jeton|secret|pare-?feu|firewall|argent|paiement|payer|abonnement|achat|fusion|\bmerge\b|\bforce\b|tailscale|serveur central|\bd[ée]p[ôo]t\b|\bmain\b|\bmaster\b", re.I)

async def approval_new(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    try: d = await req.json()
    except Exception: return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
    action, details = str(d.get("action", "")).strip()[:200], str(d.get("details", ""))[:4000]
    if not action: return JSONResponse({"erreur": "action vide"}, status_code=400)
    level = "critique" if (d.get("critique") or CRITICAL_RE.search(action + " " + details)) else "local"
    with db() as c:
        touch(c, me); expire(c)
        if c.execute("SELECT COUNT(*) n FROM approvals WHERE requester=? AND status='en attente'", (me,)).fetchone()["n"] >= MAX_PENDING:
            event("limite", f"{me} a déjà {MAX_PENDING} demandes de validation en attente", c)
            return JSONResponse({"erreur": f"{MAX_PENDING} demandes déjà en attente : attends la décision de l'humain"}, status_code=429)
        cur = c.execute("INSERT INTO approvals(requester,action,details,status,created,level) VALUES(?,?,?,?,?,?)", (me, action, details, "en attente", now(), level))
        event("validation", f"demande #{cur.lastrowid} de {me} ({level}) : {action}", c)
    qui = "le propriétaire" if (level == "critique" or me == "B") else "l'humain du PC 2 ou le propriétaire"
    return JSONResponse({"ok": True, "id": cur.lastrowid, "statut": "en attente", "niveau": level, "qui_decide": qui})

async def approval_get(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    with db() as c:
        expire(c)
        r = c.execute("SELECT * FROM approvals WHERE id=? AND requester=?", (req.path_params["aid"], me)).fetchone()
    if not r: return JSONResponse({"erreur": "demande introuvable"}, status_code=404)
    return JSONResponse({"id": r["id"], "action": r["action"], "statut": r["status"], "decidee": r["decided"], "note": r["note"]})

async def approval_decide(req: Request):
    role = human_role(req)
    if not role: return Response("Authentification requise", status_code=401)
    f = await req.form()
    try: aid = int(f.get("id"))
    except Exception: return Response("id invalide", status_code=400)
    if not hmac.compare_digest(str(f.get("csrf", "")), csrf(aid)): return Response("jeton de formulaire invalide", status_code=403)
    dec = {"approve": "approuvée", "deny": "refusée"}.get(f.get("decision"))
    if not dec: return Response("décision invalide", status_code=400)
    note = str(f.get("note", ""))[:300]
    with db() as c:
        expire(c)
        r = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if r and r["status"] == "en attente":
            if not (role == "owner" or (r["requester"] == "A" and (r["level"] or "local") == "local")):
                event("validation-refusée", f"{role} n'a pas le droit de trancher #{aid} ({r['requester']}, {r['level']})", c)
                return Response("Décision réservée au propriétaire", status_code=403)
            par = "le propriétaire" if role == "owner" else "l'humain du PC 2"
            c.execute("UPDATE approvals SET status=?, decided=?, note=?, decided_by=? WHERE id=?", (dec, now(), note, par, aid))
            body = (f"[VALIDATION #{aid}] Décision de {par} : {dec.upper()}. Action concernée : {r['action']}." + (f" Note : {note}" if note else "")
                    + (" Tu peux l'exécuter, uniquement cette action." if dec == "approuvée" else " Ne l'exécute pas."))
            c.execute("INSERT INTO messages(uid,thread,sender,recipient,priority,body,created) VALUES(?,?,?,?,?,?,?)",
                      (None, f"validation-{aid}", "H", r["requester"], "urgente", body, now()))
            event("validation", f"#{aid} {dec} par {par} pour {r['requester']}", c)
    return RedirectResponse("/ui", status_code=303)

def approvals_block(role="owner"):
    e = html.escape
    with db() as c:
        expire(c)
        pend = c.execute("SELECT * FROM approvals WHERE status='en attente' ORDER BY id").fetchall()
        hist = c.execute("SELECT * FROM approvals WHERE status!='en attente' ORDER BY id DESC LIMIT 15").fetchall()
    if role != "owner":
        pend = [a for a in pend if a["requester"] == "A"]; hist = [a for a in hist if a["requester"] == "A"]
    out = "<h3>Demandes de validation en attente</h3>"
    if not pend: out += "<p>Aucune demande en attente.</p>"
    for a in pend:
        lvl = a["level"] or "local"
        can = role == "owner" or (a["requester"] == "A" and lvl == "local")
        tag = "<span style='background:#b62324;padding:2px 8px;border-radius:10px'>CRITIQUE</span>" if lvl == "critique" else "<span style='background:#444;padding:2px 8px;border-radius:10px'>local</span>"
        out += (f"<div class=c style='display:block;margin:8px 0'><b>#{a['id']} — Claude {e(a['requester'])}</b> {tag} ({e(a['created'])})<br>"
                f"<b>Action :</b> {e(a['action'])}<br><small>{e(a['details'])}</small>")
        if can:
            out += (f"<form method=post action='/ui/decision' style='margin-top:8px'><input type=hidden name=id value={a['id']}>"
                    f"<input type=hidden name=csrf value={csrf(a['id'])}><input name=note placeholder='note (facultatif)' style='width:260px'> "
                    f"<button name=decision value=approve style='background:#1a7f37;color:#fff;border:0;padding:6px 14px;border-radius:6px'>Approuver</button> "
                    f"<button name=decision value=deny style='background:#b62324;color:#fff;border:0;padding:6px 14px;border-radius:6px'>Refuser</button></form></div>")
        else:
            out += "<p><i>Décision réservée au propriétaire (action critique).</i></p></div>"
    rows = "".join(f"<tr><td>#{a['id']}</td><td>{e(a['requester'])}</td><td>{e(a['level'] or 'local')}</td><td>{e(a['action'])}</td><td>{e(a['status'])}</td><td>{e(a['decided_by'] or '')}</td><td>{e(a['decided'] or '')}</td><td>{e(a['note'] or '')}</td></tr>" for a in hist)
    out += "<h3>Historique des validations</h3><table><tr><th>n°<th>de<th>niveau<th>action<th>décision<th>par<th>date<th>note</tr>" + (rows or "<tr><td colspan=8>aucune</td></tr>") + "</table>"
    return out

PAIR_TTL_MIN = 10          # validité d'un code d'appairage
PAIR_MAX_FAILS = 5         # échecs max par fenêtre de 10 min, tous clients confondus
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

def hash_code(code):
    return hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()

async def pair_create(req: Request):
    if not is_admin(req): return Response("Authentification requise", status_code=401)
    f = await req.form(); agent = f.get("agent")
    if agent not in AGENTS: return Response("agent invalide", status_code=400)
    if not hmac.compare_digest(str(f.get("csrf", "")), csrf("pair-" + agent)): return Response("jeton de formulaire invalide", status_code=403)
    code = "".join(secrets.choice(ALPHABET) for _ in range(10))
    exp = (datetime.datetime.now() + datetime.timedelta(minutes=PAIR_TTL_MIN)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("UPDATE pairings SET used=1 WHERE agent=? AND used=0", (agent,))   # un seul code actif par agent
        c.execute("INSERT INTO pairings(agent,code_hash,created,expires) VALUES(?,?,?,?)", (agent, hash_code(code), now(), exp))
        event("appairage", f"code généré pour {agent}, valable {PAIR_TTL_MIN} min", c)
    pretty = code[:5] + "-" + code[5:]
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8><body style="font:16px system-ui;background:#111;color:#eee;margin:30px">
<h2>Code d'appairage pour le Claude {html.escape(agent)}</h2>
<p style="font-size:34px;letter-spacing:4px;border:2px solid #444;display:inline-block;padding:12px 24px;border-radius:10px"><b>{pretty}</b></p>
<p>Valable <b>{PAIR_TTL_MIN} minutes</b>, à usage unique. Donne-le au Claude du PC concerné.<br>Cette page ne l'affichera plus : en cas de perte, génères-en un nouveau.</p>
<p><a style="color:#8ab4ff" href="/ui">← retour au suivi</a></p>""")

async def pair(req: Request):
    try: d = await req.json()
    except Exception: return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
    h = hash_code(str(d.get("code", "")))
    lim = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat(timespec="seconds")
    with db() as c:
        if c.execute("SELECT COUNT(*) n FROM events WHERE kind='appairage-échec' AND ts>=?", (lim,)).fetchone()["n"] >= PAIR_MAX_FAILS:
            return JSONResponse({"erreur": "trop d'essais ratés : réessaie dans 10 minutes"}, status_code=429)
        found = None
        for r in c.execute("SELECT * FROM pairings WHERE used=0 AND expires>=?", (now(),)).fetchall():
            if hmac.compare_digest(r["code_hash"], h): found = r
        if not found:
            event("appairage-échec", "code invalide ou expiré", c)
            return JSONResponse({"erreur": "code invalide ou expiré"}, status_code=403)
        c.execute("UPDATE pairings SET used=1 WHERE id=?", (found["id"],))
        event("appairage", f"un PC s'est appairé comme Claude {found['agent']}", c)
    return JSONResponse({"url": f"http://{CFG['host']}:{CFG['port']}", "token": TOKENS[found["agent"]], "agent": found["agent"]})

def pairing_block():
    forms = "".join(f"<form method=post action='/ui/pair-create' style='display:inline-block;margin-right:10px'><input type=hidden name=agent value={a}><input type=hidden name=csrf value={csrf('pair-' + a)}>"
                    f"<button style='background:#1f6feb;color:#fff;border:0;padding:8px 16px;border-radius:6px'>Générer un code pour le Claude {a}</button></form>" for a in AGENTS)
    return f"<h3>Appairer un PC (sans clé USB)</h3><p><small>Code à usage unique, valable {PAIR_TTL_MIN} min. À donner au Claude de l'autre PC.</small></p>{forms}"

INVITE_TTL_MIN = 10

def migrate():
    with db() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(approvals)")]
        if "level" not in cols: c.execute("ALTER TABLE approvals ADD COLUMN level TEXT DEFAULT 'local'")
        if "decided_by" not in cols: c.execute("ALTER TABLE approvals ADD COLUMN decided_by TEXT")
        c.executescript("""CREATE TABLE IF NOT EXISTS humans(name TEXT PRIMARY KEY, salt TEXT, hash TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT, created TEXT, expires TEXT, used INTEGER DEFAULT 0);""")

def invite_block():
    return (f"<h3>Inviter l'humain du PC 2</h3><p><small>Génère un code (valable {INVITE_TTL_MIN} min, usage unique). La personne ouvre http://{CFG['host']}:{CFG['port']}/invite sur son PC et choisit elle-même son mot de passe : tu ne le connaîtras pas.</small></p>"
            f"<form method=post action='/ui/invite-create'><input type=hidden name=csrf value={csrf('invite')}>"
            "<button style='background:#8957e5;color:#fff;border:0;padding:8px 16px;border-radius:6px'>Générer un code d'invitation</button></form>")

async def invite_create(req: Request):
    if not is_admin(req): return Response("Réservé au propriétaire", status_code=401)
    f = await req.form()
    if not hmac.compare_digest(str(f.get("csrf", "")), csrf("invite")): return Response("jeton de formulaire invalide", status_code=403)
    code = "".join(secrets.choice(ALPHABET) for _ in range(10))
    exp = (datetime.datetime.now() + datetime.timedelta(minutes=INVITE_TTL_MIN)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("UPDATE invites SET used=1 WHERE used=0")
        c.execute("INSERT INTO invites(code_hash,created,expires) VALUES(?,?,?)", (hash_code(code), now(), exp))
        event("invitation", f"code d'invitation généré pour l'humain du PC 2 (valable {INVITE_TTL_MIN} min)", c)
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8><body style="font:16px system-ui;background:#111;color:#eee;margin:30px">
<h2>Code d'invitation pour l'humain du PC 2</h2><p style="font-size:34px;letter-spacing:4px;border:2px solid #444;display:inline-block;padding:12px 24px;border-radius:10px"><b>{code[:5]}-{code[5:]}</b></p>
<p>Valable <b>{INVITE_TTL_MIN} minutes</b>, usage unique. Cette page ne l'affichera plus.</p><p><a style="color:#8ab4ff" href="/ui">← retour</a></p>""")

def invite_form(msg=""):
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8><body style="font:16px system-ui;background:#111;color:#eee;margin:30px;max-width:520px">
<h2>IA-partag — créer ton accès</h2><p>Saisis le code reçu du propriétaire et choisis ton mot de passe (12 caractères minimum). Ton identifiant sera <b>humain-A</b>.</p>
<p style="color:#ff8b8b">{html.escape(msg)}</p>
<form method=post action="/invite"><p><input name=code placeholder="Code (XXXXX-XXXXX)" style="width:100%;padding:8px"></p>
<p><input type=password name=pw placeholder="Mot de passe" style="width:100%;padding:8px"></p>
<p><input type=password name=pw2 placeholder="Confirmation" style="width:100%;padding:8px"></p>
<button style="background:#1f6feb;color:#fff;border:0;padding:10px 20px;border-radius:6px">Créer mon accès</button></form>""")

async def invite(req: Request):
    if req.method == "GET": return invite_form()
    f = await req.form(); pw, pw2 = str(f.get("pw", "")), str(f.get("pw2", ""))
    lim = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat(timespec="seconds")
    with db() as c:
        if c.execute("SELECT COUNT(*) n FROM events WHERE kind='invitation-échec' AND ts>=?", (lim,)).fetchone()["n"] >= PAIR_MAX_FAILS:
            return invite_form("Trop d'essais ratés : réessaie dans 10 minutes.")
        found = None
        for r in c.execute("SELECT * FROM invites WHERE used=0 AND expires>=?", (now(),)).fetchall():
            if hmac.compare_digest(r["code_hash"], hash_code(str(f.get("code", "")))): found = r
        if not found:
            event("invitation-échec", "code d'invitation invalide ou expiré", c)
            return invite_form("Code invalide ou expiré.")
        if len(pw) < 12 or pw != pw2:
            return invite_form("Mot de passe trop court (12 minimum) ou confirmation différente. Le code reste valable.")
        salt = secrets.token_bytes(16)
        c.execute("INSERT OR REPLACE INTO humans(name,salt,hash,created) VALUES('humain-A',?,?,?)", (salt.hex(), pw_hash(pw, salt), now()))
        c.execute("UPDATE invites SET used=1 WHERE id=?", (found["id"],))
        event("invitation", "l'humain du PC 2 a créé son accès (humain-A)", c)
    return HTMLResponse("""<!doctype html><meta charset=utf-8><body style="font:16px system-ui;background:#111;color:#eee;margin:30px"><h2>Accès créé</h2>
<p>Identifiant : <b>humain-A</b>. Va sur <a style="color:#8ab4ff" href="/ui">/ui</a> et connecte-toi avec ton mot de passe.</p>""")

import threading
MEM_MAX_KEYS, MEM_MAX_VALUE, MEM_MAX_KEY = 200, 8000, 100
SECRET_RE = re.compile(r"(mot de passe|motdepasse|password|passwd|pwd|jeton|token|secret|api[_ -]?key|cl[ée] priv[ée]e|private key)\s*[:=]|-----BEGIN|[A-Za-z0-9]{40,}", re.I)
BACKUP_DIR = ROOT / "backups"
BACKUP_EVERY_H = 6

def mem_init():
    with db() as c:
        c.executescript("""CREATE TABLE IF NOT EXISTS memory(key TEXT PRIMARY KEY, value TEXT, author TEXT, updated TEXT, version INTEGER);
            CREATE TABLE IF NOT EXISTS memory_history(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, value TEXT, author TEXT, ts TEXT, version INTEGER);""")

async def memory_get(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    key = req.query_params.get("key", "")
    with db() as c:
        touch(c, me)
        if key:
            r = c.execute("SELECT * FROM memory WHERE key=?", (key,)).fetchone()
            if not r: return JSONResponse({"erreur": "clé introuvable"}, status_code=404)
            return JSONResponse({"key": r["key"], "value": r["value"], "version": r["version"], "auteur": r["author"], "maj": r["updated"]})
        pre = req.query_params.get("prefix", "")
        rows = c.execute("SELECT key,version,author,updated FROM memory WHERE key LIKE ? ESCAPE '\\' ORDER BY key", (pre.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
    return JSONResponse({"cles": [{"key": r["key"], "version": r["version"], "auteur": r["author"], "maj": r["updated"]} for r in rows]})

async def memory_put(req: Request):
    me = agent_of(req)
    if not me: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    try: d = await req.json()
    except Exception: return JSONResponse({"erreur": "JSON invalide"}, status_code=400)
    key, value = str(d.get("key", "")).strip(), str(d.get("value", ""))
    try: expected = int(d.get("version", 0))
    except Exception: return JSONResponse({"erreur": "version invalide"}, status_code=400)
    if not key or len(key) > MEM_MAX_KEY: return JSONResponse({"erreur": f"clé vide ou trop longue (max {MEM_MAX_KEY})"}, status_code=400)
    if len(value) > MEM_MAX_VALUE: return JSONResponse({"erreur": f"valeur trop longue (max {MEM_MAX_VALUE})"}, status_code=400)
    if any(t and t in value for t in TOKENS.values()):
        event("memoire-refusée", f"{me} a tenté d'écrire un secret dans la mémoire (clé {key[:40]})")
        return JSONResponse({"erreur": "refusé : la valeur contient un secret (jeton ou mot de passe)"}, status_code=400)
    if SECRET_RE.search(value):
        event("memoire-refusée", f"{me} : valeur ressemblant à un secret refusée (clé {key[:40]})")
        return JSONResponse({"erreur": "refusé : la valeur ressemble à un secret (mot de passe, jeton, clé d'API). Ne mets jamais de secret dans la mémoire partagée ; décris-le sans le recopier."}, status_code=400)
    with db() as c:
        touch(c, me)
        r = c.execute("SELECT * FROM memory WHERE key=?", (key,)).fetchone()
        cur = r["version"] if r else 0
        if expected != cur:
            return JSONResponse({"erreur": "conflit : la clé a changé depuis ta lecture", "version_actuelle": cur, "valeur_actuelle": r["value"] if r else None}, status_code=409)
        if not r and c.execute("SELECT COUNT(*) n FROM memory").fetchone()["n"] >= MEM_MAX_KEYS:
            return JSONResponse({"erreur": f"mémoire pleine ({MEM_MAX_KEYS} clés max)"}, status_code=429)
        nv = cur + 1
        c.execute("INSERT INTO memory(key,value,author,updated,version) VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, author=excluded.author, updated=excluded.updated, version=excluded.version", (key, value, me, now(), nv))
        c.execute("INSERT INTO memory_history(key,value,author,ts,version) VALUES(?,?,?,?,?)", (key, value, me, now(), nv))
        event("memoire", f"{me} a écrit la clé {key[:60]} (v{nv})", c)
    return JSONResponse({"ok": True, "key": key, "version": nv})

def memory_block():
    e = html.escape
    with db() as c: rows = c.execute("SELECT key,version,author,updated,substr(value,1,120) v FROM memory ORDER BY updated DESC LIMIT 20").fetchall()
    body = "".join(f"<tr><td>{e(r['key'])}</td><td>v{r['version']}</td><td>{e(r['author'])}</td><td>{e(r['updated'])}</td><td>{e(r['v'])}</td></tr>" for r in rows)
    return "<h3>Mémoire partagée (lecture seule)</h3><table><tr><th>clé<th>version<th>auteur<th>maj<th>début de la valeur</tr>" + (body or "<tr><td colspan=5>vide</td></tr>") + "</table>"

def backup_now(tag="auto"):
    BACKUP_DIR.mkdir(exist_ok=True)
    dest = BACKUP_DIR / f"bus-{datetime.datetime.now():%Y%m%d-%H%M%S}-{tag}.db"
    src = sqlite3.connect(DB); dst = sqlite3.connect(dest)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    event("sauvegarde", f"copie de la base : {dest.name}")
    return dest

def backup_loop():
    import time
    while True:
        try: backup_now()
        except Exception as ex: logging.error("sauvegarde échouée : %s", ex)
        time.sleep(BACKUP_EVERY_H * 3600)

async def backup_latest(req: Request):
    """Derniere sauvegarde de la base, reservee a l'agent A (PC 2) : copie hors du PC 1."""
    if agent_of(req) != "A": return JSONResponse({"erreur": "non autoris\u00e9"}, status_code=401)
    files = sorted(BACKUP_DIR.glob("bus-*.db"))
    if not files: return JSONResponse({"erreur": "aucune sauvegarde"}, status_code=404)
    f = files[-1]
    with db() as c: event("sauvegarde", f"copie {f.name} envoy\u00e9e au PC 2", c)
    return Response(f.read_bytes(), media_type="application/octet-stream", headers={"X-Backup-Name": f.name})

init()
migrate()
mem_init()
threading.Thread(target=backup_loop, daemon=True).start()
app = Starlette(routes=[Route("/send", send, methods=["POST"]), Route("/inbox", inbox), Route("/heartbeat", heartbeat, methods=["POST"]),
                        Route("/status", status), Route("/backup/latest", backup_latest), Route("/approval", approval_new, methods=["POST"]), Route("/memory", memory_get), Route("/memory/put", memory_put, methods=["POST"]), Route("/approval/{aid:int}", approval_get), Route("/ui/decision", approval_decide, methods=["POST"]), Route("/ui/pair-create", pair_create, methods=["POST"]), Route("/ui/invite-create", invite_create, methods=["POST"]), Route("/invite", invite, methods=["GET", "POST"]), Route("/pair", pair, methods=["POST"]), Route("/ui", ui), Route("/", ui)])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG["host"], port=CFG["port"], log_level="warning")
