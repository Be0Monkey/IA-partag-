import re
"""Serveur central IA-partag (phase 0) : messages, présence, auth par jeton, interface de suivi minimale."""
import base64, datetime, difflib, hashlib, hmac, html, json, logging, math, pathlib, secrets, sqlite3, sys
from urllib.parse import parse_qs, quote, urlencode
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

def form_data(req):
    """Lit un formulaire urlencoded sans dépendance externe (pas besoin de python-multipart)."""
    async def _read():
        raw = (await req.body()).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
    return _read()

HUM_MAX_FAILS = 5       # échecs d'identifiants max par client (adresse + nom saisi) sur la fenêtre
HUM_WINDOW_MIN = 10     # fenêtre de comptage et durée du blocage, en minutes

class HumanBlocked(Exception):
    """Client bloqué après trop d'échecs d'identifiants (limite par client, pas globale)."""
    def __init__(self, minutes, user):
        self.minutes, self.user = minutes, user

def _basic(h):
    try:
        user, pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
        return user, pw
    except Exception:
        return None, None

def human_role(req):
    """Vérifie les identifiants humains. Lève HumanBlocked si ce client a échoué trop souvent."""
    h = req.headers.get("authorization", "")
    if not h.startswith("Basic "): return None
    user, _ = _basic(h)
    ip = req.client.host if req.client else "?"
    key = f"{ip}|{user or '?'}"
    lim = (datetime.datetime.now() - datetime.timedelta(minutes=HUM_WINDOW_MIN)).isoformat(timespec="seconds")
    with db() as c:
        rows = c.execute("SELECT ts, detail FROM events WHERE kind='humain-échec' AND ts>=? ORDER BY ts", (lim,)).fetchall()
    mine = [r["ts"] for r in rows if r["detail"].startswith(f"client={key};")]
    if len(mine) >= HUM_MAX_FAILS:
        # le blocage se lève quand il ne reste plus assez d'échecs dans la fenêtre
        fin = datetime.datetime.fromisoformat(mine[-HUM_MAX_FAILS]) + datetime.timedelta(minutes=HUM_WINDOW_MIN)
        minutes = max(1, math.ceil((fin - datetime.datetime.now()).total_seconds() / 60))
        raise HumanBlocked(minutes, user or "?")
    r = _human_role_check(h)
    if r is None: event("humain-échec", f"client={key}; identifiants invalides")
    return r

def _human_role_check(h):
    """'owner' (identifiant humain), 'humain-A' (humain du PC 2) ou None."""
    user, pw = _basic(h)
    if user is None: return None
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

async def ui_state(req: Request):
    """Résumé léger pour l'actualisation de /ui : sert à détecter les nouveautés sans recharger la page."""
    role = human_role(req)
    if not role: return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    try: depuis = int(req.query_params.get("depuis", "0"))
    except ValueError: depuis = 0
    with db() as c:
        expire(c)
        m = c.execute("SELECT COALESCE(MAX(id),0) m FROM messages").fetchone()["m"]
        nouv = c.execute("SELECT COUNT(*) n FROM messages WHERE id>?", (depuis,)).fetchone()["n"]
        e = c.execute("SELECT COALESCE(MAX(id),0) m FROM events").fetchone()["m"]
        ap = c.execute("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM approvals WHERE status='en attente'").fetchone()
    return JSONResponse({"messages": m, "nouveaux_messages": nouv, "evenements": e, "en_attente": ap["n"], "derniere_demande": ap["m"]})

def ago(ts):
    """Âge lisible d'un horodatage ISO (« il y a 3 min »)."""
    if not ts: return "jamais"
    s = (datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds()
    if s < 60: return f"il y a {max(0, int(s))} s"
    if s < 3600: return f"il y a {int(s // 60)} min"
    if s < 86400: return f"il y a {int(s // 3600)} h {int(s % 3600 // 60):02d} min"
    return f"il y a {int(s // 86400)} j"

INCIDENTS = ("anti-boucle", "limite", "humain-échec", "appairage-échec", "invitation-échec", "validation-refusée", "memoire-refusée", "sauvegarde-échec")

def url_with(q, **kw):
    """Adresse de /ui avec les paramètres courants (filtres, fil ouvert) plus les changements demandés."""
    d = dict(q); d.update(kw)
    return "/ui?" + urlencode({k: v for k, v in d.items() if v})

def cartes_block(ags, unread):
    e = html.escape
    with db() as c:
        oldest = {r["recipient"]: r["t"] for r in c.execute("SELECT recipient, MIN(created) t FROM messages WHERE delivered IS NULL GROUP BY recipient")}
        pend = {r["requester"]: r["n"] for r in c.execute("SELECT requester, COUNT(*) n FROM approvals WHERE status='en attente' GROUP BY requester")}
    out = ""
    for a in AGENTS:
        v = ags[a]; n = unread.get(a, 0)
        recup = f" (le plus ancien : {ago(oldest[a])})" if n else ""
        out += (f"<div class='carte {a}'><span class='badge {a}'>{a}</span> <b>Claude {a}</b> — {'🟢 connecté' if v['connecte'] else '🔴 hors ligne'}<br>"
                f"<small>Dernier contact : {ago(v['vu'])} ({e(v['vu'] or 'jamais')})</small><br>"
                f"Messages à récupérer : <b>{n}</b>{e(recup)}<br>"
                f"<small>Demandes de validation en attente : {pend.get(a, 0)}/{MAX_PENDING}</small></div>")
    return out

def _sens(paires):
    ps = set(p for p in (paires or "").split(",") if p)
    if "A>B" in ps and "B>A" in ps: return "A ↔ B"
    return ", ".join(sorted(p.replace(">", " → ") for p in ps))

def _garde_msg(m, q):
    """Filtres de la liste de messages : urgents, à récupérer, expéditeur A ou B."""
    f, s = q.get("filtre"), q.get("sens")
    if f == "urgents" and m["priority"] != "urgente": return False
    if f == "recuperer" and m["delivered"]: return False
    if s in ("A", "B") and m["sender"] != s: return False
    return True

def _cond_filtre(q):
    conds, args = [], []
    f, s = q.get("filtre"), q.get("sens")
    if f == "urgents": conds.append("priority='urgente'")
    if f == "recuperer": conds.append("delivered IS NULL")
    if s in ("A", "B"): conds.append("sender=?"); args.append(s)
    return (" AND ".join(conds) or "1=1"), args

def filtres_bar(q):
    e = html.escape
    f, s = q.get("filtre", ""), q.get("sens", "")
    def lien(libelle, actif, nouveaux):
        etat = {k: v for k, v in {"filtre": nouveaux.get("filtre", f), "sens": nouveaux.get("sens", s)}.items() if v}
        cls = " class=actif aria-current=true" if actif else ""
        return f"<a href='{e(url_with(q, **nouveaux))}' data-filtre='{e(urlencode(etat))}'{cls}>{libelle}</a>"
    liens = [lien("Tous", not f and not s, {"filtre": None}),
             lien("Urgents", f == "urgents", {"filtre": "urgents"}),
             lien("À récupérer", f == "recuperer", {"filtre": "recuperer"}),
             lien("Envoyés par A", s == "A", {"sens": "A"}),
             lien("Envoyés par B", s == "B", {"sens": "B"})]
    actifs = [x for x in (f, s) if x]
    actif_txt = f"<b>Filtre actif : {e(' + '.join(actifs))}</b> · " if actifs else ""
    reset = f" · <a href='{e(url_with(q, filtre=None, sens=None, journal=None))}' data-filtre=''>Réinitialiser</a>" if actifs else ""
    return f"<p class=filtres>{actif_txt}{' · '.join(liens)}{reset}</p>"

def fils_block(q):
    e = html.escape
    cond, args = _cond_filtre(q)
    with db() as c:
        lst = c.execute("SELECT thread, COUNT(*) n, MAX(id) mid, GROUP_CONCAT(DISTINCT sender || '>' || recipient) paires "
                        f"FROM messages WHERE thread IN (SELECT thread FROM messages WHERE {cond}) GROUP BY thread ORDER BY mid DESC LIMIT 40", args).fetchall()
        rows = ""
        for t in lst:
            dern = c.execute("SELECT created, sender, body FROM messages WHERE thread=? ORDER BY id DESC LIMIT 1", (t["thread"],)).fetchone()
            apercu = dern["body"][:80] + ("…" if len(dern["body"]) > 80 else "")
            bloque = " <span class='etiq critique'>bloqué</span>" if t["n"] >= MAX_PER_THREAD else ""
            sel = q.get("fil") == t["thread"]
            rows += (f"<tr class='{'sel' if sel else ''}'><td><a href='{e(url_with(q, fil=t['thread']))}'{' aria-current=true' if sel else ''}>{e(t['thread'])}</a></td>"
                     f"<td>{e(_sens(t['paires']))}</td><td>{t['n']}/{MAX_PER_THREAD}{bloque}</td>"
                     f"<td><small>{e(dern['created'])} · {e(dern['sender'])}</small><br>{e(apercu)}</td></tr>")
    return ("<h3>Fils de discussion (40 plus récents)</h3><div class=table-wrap><table><tr><th>fil<th>sens<th>messages<th>dernier message</tr>"
            + (rows or "<tr><td colspan=4>aucun fil pour ce filtre</td></tr>") + "</table></div>")

def fil_detail(q, thread):
    e = html.escape
    if not thread:
        return "<h3>Détail du fil</h3><p>Choisis un fil dans la liste pour lire tous ses messages, dans l'ordre.</p>"
    with db() as c:
        msgs = c.execute("SELECT * FROM messages WHERE thread=? ORDER BY id", (thread,)).fetchall()
    retour = f"<p><a href='{e(url_with(q, fil=None))}'>← fermer le fil</a></p>"
    if not msgs: return f"<h3>Fil introuvable</h3>{retour}<p>Aucun message dans le fil <b>{e(thread)}</b>.</p>"
    n = len(msgs)
    etat = " — <span class='etiq critique'>fil bloqué (anti-boucle)</span>" if n >= MAX_PER_THREAD else ""
    lien = e(f"/ui?fil={quote(thread)}")
    entete = (f"<h3>Fil {e(thread)} : {n}/{MAX_PER_THREAD} messages{etat}</h3>"
              f"<p><button class='bouton b-bleu' type=button data-copie-lien='{lien}'>Copier le lien du fil</button></p>{retour}")
    items = "".join(
        f"<div class=bloc><small>{e(m['created'])} · {e(m['sender'])} → {e(m['recipient'])} · {e(m['priority'])} · {'Récupérée' if m['delivered'] else 'À récupérer'}</small>"
        f"<div style='white-space:pre-wrap;margin:6px 0'>{e(m['body'])}</div>"
        f"<button class='bouton b-gris' type=button data-copie-texte='{e(m['body'])}'>Copier le texte</button></div>" for m in msgs)
    return entete + items

def journal_block(q):
    e = html.escape
    incidents = q.get("journal") == "incidents"
    with db() as c:
        if incidents:
            evs = c.execute(f"SELECT * FROM events WHERE kind IN ({','.join('?' * len(INCIDENTS))}) ORDER BY id DESC LIMIT 30", INCIDENTS).fetchall()
        else:
            evs = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 15").fetchall()
        ok = c.execute("SELECT ts FROM events WHERE kind='sauvegarde' AND detail LIKE 'copie de la base%' ORDER BY id DESC LIMIT 1").fetchone()
        ko = c.execute("SELECT ts, detail FROM events WHERE kind='sauvegarde-échec' ORDER BY id DESC LIMIT 1").fetchone()
        rows = ""
        for x in evs:
            detail = e(x["detail"]); extra = ""
            m = re.search(r"fil (\S+) bloqué", x["detail"])
            if x["kind"] == "anti-boucle" and m:
                n = c.execute("SELECT COUNT(*) n FROM messages WHERE thread=?", (m.group(1),)).fetchone()["n"]
                extra = (f" · <a href='{e(url_with(q, fil=m.group(1)))}'>Voir le fil</a> · état actuel : {n}/{MAX_PER_THREAD}"
                         + (" (toujours bloqué)" if n >= MAX_PER_THREAD else " (plus bloqué)"))
            elif (datetime.datetime.now() - datetime.datetime.fromisoformat(x["ts"])).total_seconds() > 600:
                extra = " · <small>ancien (passé)</small>"
            rows += f"<tr><td>{e(x['ts'])}</td><td>{e(x['kind'])}</td><td>{detail}{extra}</td></tr>"
    if ko and (not ok or ko["ts"] > ok["ts"]):
        bandeau = f"<div class='resultat ko'>Échec de sauvegarde le {e(ko['ts'])} : {e(ko['detail'])}. Dernière sauvegarde réussie : {e(ok['ts'] if ok else 'aucune')}.</div>"
    else:
        bandeau = f"<p>Dernière sauvegarde réussie : <b>{e(ok['ts'] if ok else 'aucune')}</b> ({ago(ok['ts'] if ok else None)})</p>"
    filtres = (f"<p><a href='{e(url_with(q, journal=None))}'>Tout</a> · <a href='{e(url_with(q, journal='incidents'))}'>Incidents</a> "
               f"· affichage : <b>{'incidents' if incidents else 'tout'}</b></p>")
    return (f"<h3>Journal d'événements</h3>{bandeau}{filtres}<div class=table-wrap><table><tr><th>date<th>type<th>détail</tr>"
            + (rows or "<tr><td colspan=3>aucun</td></tr>") + "</table></div>")

CSS = """
:root{--bg:#111;--fg:#eee;--line:#333;--card:#1b1b1b;--muted:#9aa0a6;--A:#7DD3FC;--B:#C4B5FD;--ok:#1a7f37;--ko:#b62324;--btn:#1f6feb}
*{box-sizing:border-box}
body{font:14px system-ui,sans-serif;margin:0;padding:16px;background:var(--bg);color:var(--fg)}
a{color:#8ab4ff}
h2{margin:0 0 4px;font-size:20px} h3{margin:18px 0 8px;font-size:16px}
header.top{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:10px}
header.top .meta{color:var(--muted);font-size:13px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}
.carte{flex:1 1 280px;min-width:0;border:2px solid var(--line);border-radius:10px;padding:12px;background:var(--card)}
.carte.A{border-color:var(--A)} .carte.B{border-color:var(--B)}
.badge{display:inline-block;min-width:26px;text-align:center;border-radius:13px;padding:2px 8px;font-weight:700;color:#111}
.badge.A{background:var(--A)} .badge.B{background:var(--B)}
.bloc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:10px 0;min-width:0}
.urgent{border-color:#b62324}
.etiq{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#444}
.etiq.critique{background:var(--ko)}
.bouton{border:0;padding:7px 14px;border-radius:6px;color:#fff;cursor:pointer;font:inherit;margin:2px 0}
.b-ok{background:var(--ok)} .b-ko{background:var(--ko)} .b-bleu{background:var(--btn)} .b-gris{background:#444}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #ffd54f;outline-offset:2px}
input.note,input.cherche{width:100%;max-width:340px;padding:6px;background:#0d0d0d;color:var(--fg);border:1px solid #555;border-radius:6px}
.compteur{font-size:12px;color:var(--muted)}
.banniere{position:sticky;top:0;z-index:5;background:#263238;border:1px solid #4f6d7a;border-radius:8px;padding:8px 12px;margin:8px 0;display:none}
.banniere.on{display:block}
.resultat{background:#1e2d1e;border:1px solid var(--ok);border-radius:8px;padding:10px;margin:10px 0}
.resultat.ko{background:#2d1e1e;border-color:var(--ko)}
.filtres{margin:6px 0 10px}
.filtres a.actif{font-weight:700;color:#fff;text-decoration:underline}
.table-wrap{overflow-x:auto;margin:8px 0 20px}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #333;padding:4px 8px;text-align:left;vertical-align:top;word-break:break-word}
th{color:var(--muted);font-weight:600}
tr.sel td{background:#1d2a3a}
small,.petit{color:var(--muted)}
details{margin:6px 0}
summary{cursor:pointer;font-weight:600;padding:4px 0}
.fils-grille{display:grid;grid-template-columns:1fr;gap:12px;align-items:start}
.fils-grille>div{min-width:0}
@media (min-width:1000px){.fils-grille{grid-template-columns:minmax(340px,1fr) minmax(0,2fr)}}
#ligne-detail>td{padding:8px 0;border-bottom:0}
.valeur{white-space:pre-wrap;word-break:break-word;max-height:220px;overflow:auto;background:#0d0d0d;border:1px solid #333;border-radius:6px;padding:6px;margin:0;font:13px ui-monospace,monospace}
.diff-plus{color:#7ee787} .diff-moins{color:#ff9b9b}
body.compact td,body.compact th{padding:2px 6px}
body.compact .bloc{padding:6px 8px;margin:6px 0}
@media (max-width:640px){body{padding:12px 16px}.carte{flex-basis:100%}}
"""

JS = """
(function(){
  var st = document.getElementById('etat');
  var boutonPause = document.getElementById('bouton-pause');
  var boutonDensite = document.getElementById('bouton-densite');
  var statut = document.getElementById('statut-maj');
  var heure = document.getElementById('heure-maj');
  var banniere = document.getElementById('banniere');
  var banniereTexte = document.getElementById('banniere-texte');
  var base = st ? {m: +st.dataset.messages, e: +st.dataset.evenements, n: +st.dataset.enattente, d: +st.dataset.derniere} : null;
  var pauseManuelle = false;
  var prefs = {};
  try { prefs = JSON.parse(localStorage.getItem('iapartag-prefs') || '{}') || {}; } catch (x) { prefs = {}; }
  function sauverPrefs() { try { localStorage.setItem('iapartag-prefs', JSON.stringify(prefs)); } catch (x) {} }
  try { pauseManuelle = sessionStorage.getItem('iapartag-pause') === '1'; } catch (x) {}

  // Préférences : densité, sections repliées, dernier filtre de messages (mémorisées par navigateur)
  function majDensite() {
    document.body.classList.toggle('compact', prefs.densite === 'compacte');
    if (boutonDensite) boutonDensite.textContent = prefs.densite === 'compacte' ? 'Densité : compacte' : 'Densité : normale';
  }
  if (boutonDensite) boutonDensite.addEventListener('click', function () {
    prefs.densite = prefs.densite === 'compacte' ? 'normale' : 'compacte'; sauverPrefs(); majDensite();
  });
  majDensite();
  prefs.sections = prefs.sections || {};
  document.querySelectorAll('details[data-memo]').forEach(function (d) {
    var id = d.getAttribute('data-memo');
    if (prefs.sections[id] === 'ferme') d.removeAttribute('open');
    else if (prefs.sections[id] === 'ouvert') d.setAttribute('open', '');
    d.addEventListener('toggle', function () { prefs.sections[id] = d.open ? 'ouvert' : 'ferme'; sauverPrefs(); });
  });
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest ? ev.target.closest('a[data-filtre]') : null;
    if (a) { prefs.filtre = a.getAttribute('data-filtre'); sauverPrefs(); }
  });
  try {
    var dejaApplique = sessionStorage.getItem('iapartag-filtre-applique') === '1';
    if (!dejaApplique && !/[?&](filtre|sens)=/.test(location.search) && prefs.filtre) {
      sessionStorage.setItem('iapartag-filtre-applique', '1');
      location.replace('/ui?' + prefs.filtre);
    }
  } catch (x) {}

  // Copie : lien du fil ou texte d'un message, avec retour « Copié »
  function repli(texte) {
    var t = document.createElement('textarea');
    t.value = texte; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); } catch (x) {}
    document.body.removeChild(t);
  }
  function copier(texte, b) {
    var fait = function () {
      var ancien = b.textContent; b.textContent = 'Copié';
      setTimeout(function () { b.textContent = ancien; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texte).then(fait, function () { repli(texte); fait(); });
    } else { repli(texte); fait(); }
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-copie-lien],[data-copie-texte]') : null;
    if (!b) return;
    var texte = b.hasAttribute('data-copie-lien') ? location.origin + b.getAttribute('data-copie-lien') : b.getAttribute('data-copie-texte');
    copier(texte, b);
  });

  // Sur écran étroit, le détail du fil se place sous la ligne sélectionnée ; sinon à droite de la liste
  function placerDetail() {
    var d = document.getElementById('fil-detail'), zone = document.getElementById('zone-detail');
    if (!d || !zone) return;
    var ligne = document.getElementById('ligne-detail');
    var sel = document.querySelector('tr.sel');
    if (window.matchMedia('(max-width: 999px)').matches && sel) {
      if (!ligne) {
        ligne = document.createElement('tr'); ligne.id = 'ligne-detail';
        var td = document.createElement('td'); td.colSpan = 4; ligne.appendChild(td);
      }
      sel.parentNode.insertBefore(ligne, sel.nextSibling);
      ligne.firstChild.appendChild(d);
    } else {
      zone.appendChild(d);
      if (ligne && ligne.parentNode) ligne.parentNode.removeChild(ligne);
    }
  }
  placerDetail();
  var tRedim = null;
  window.addEventListener('resize', function () { clearTimeout(tRedim); tRedim = setTimeout(placerDetail, 150); });

  function saisie() {
    var el = document.activeElement;
    if (el && (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && /^(text|search|password|number)$/.test(el.type)))) return true;
    var notes = document.querySelectorAll('input.note');
    for (var i = 0; i < notes.length; i++) if (notes[i].value.trim() !== '') return true;
    return false;
  }
  function majPause() {
    if (boutonPause) { boutonPause.textContent = pauseManuelle ? 'Reprendre' : 'Pause'; boutonPause.setAttribute('aria-pressed', pauseManuelle ? 'true' : 'false'); }
  }
  function afficherStatut(texte) { if (statut) statut.textContent = texte; }
  function sauverDefilement() {
    try {
      sessionStorage.setItem('iapartag-defilement', String(window.scrollY));
      sessionStorage.setItem('iapartag-focus', (document.activeElement && document.activeElement.id) || '');
    } catch (x) {}
  }
  window.iapartagRecharger = function () { sauverDefilement(); location.reload(); };
  if (boutonPause) boutonPause.addEventListener('click', function () {
    pauseManuelle = !pauseManuelle;
    try { sessionStorage.setItem('iapartag-pause', pauseManuelle ? '1' : '0'); } catch (x) {}
    majPause(); if (!pauseManuelle) tick();
  });
  document.addEventListener('submit', sauverDefilement);
  try {
    var y = sessionStorage.getItem('iapartag-defilement');
    if (y !== null) {
      window.scrollTo(0, +y); sessionStorage.removeItem('iapartag-defilement');
      var f = sessionStorage.getItem('iapartag-focus'); sessionStorage.removeItem('iapartag-focus');
      var el = f ? document.getElementById(f) : null;
      if (el && el.focus) el.focus({preventScroll: true});
    }
  } catch (x) {}
  document.querySelectorAll('input.note').forEach(function (n) {
    var c = document.getElementById('compteur-' + n.dataset.id);
    n.addEventListener('input', function () { if (c) c.textContent = n.value.length + ' / 300'; });
  });

  function tick() {
    var raison = pauseManuelle ? 'Actualisation en pause (manuelle)' : (saisie() ? 'Actualisation en pause : saisie en cours' : null);
    if (raison) return afficherStatut(raison + ' — la saisie est conservée.');
    if (!base) return;
    fetch('/ui/etat?depuis=' + base.m, {cache: 'no-store', credentials: 'same-origin'}).then(function (r) {
      if (r.status === 401) throw new Error('auth');
      if (!r.ok) throw new Error('http');
      return r.json();
    }).then(function (d) {
      if (heure) heure.textContent = new Date().toLocaleTimeString();
      var nouveau = d.nouveaux_messages > 0 || d.derniere_demande > base.d || d.en_attente > base.n;
      var txt = [];
      if (d.nouveaux_messages > 0) txt.push(d.nouveaux_messages + (d.nouveaux_messages > 1 ? ' nouveaux messages' : ' nouveau message'));
      if (d.en_attente > base.n) txt.push('demande(s) de validation à examiner');
      if (banniereTexte) banniereTexte.textContent = txt.length ? txt.join(' · ') : 'Nouveautés disponibles.';
      if (banniere) banniere.classList.toggle('on', nouveau);
      afficherStatut(nouveau ? 'Nouveautés disponibles' : 'Actualisation automatique');
    }).catch(function (err) {
      afficherStatut(err.message === 'auth' ? 'Session expirée : recharge la page pour te reconnecter' : 'Serveur injoignable, nouvel essai dans 5 s');
    });
  }
  majPause();
  setInterval(tick, 5000);
})();
"""

def _page(body, title="IA-partag"):
    return (f"<!doctype html><html lang=fr><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style>{body}<script>{JS}</script></html>")

def _libelle_role(role):
    return "propriétaire" if role == "owner" else "humain du PC 2 (humain-A)"

def _date(s):
    return html.escape(s or "jamais")

def res_banner(q):
    """Résultat réel d'une décision, relu en base (évite d'afficher un succès inventé)."""
    res = q.get("res", "")
    try: aid = int(q.get("id", ""))
    except Exception: return ""
    if res not in ("ok", "deja", "expiree", "introuvable", "interdit"): return ""
    with db() as c: r = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    if res == "introuvable" or not r:
        return f"<div class='resultat ko'>Demande #{aid} introuvable.</div>"
    par = html.escape(r["decided_by"] or "personne")
    if res == "ok":
        return f"<div class=resultat>Décision enregistrée pour la demande #{aid} : <b>{html.escape(r['status'])}</b> par {par}, le {_date(r['decided'])}.</div>"
    if res == "deja":
        return (f"<div class='resultat ko'>La demande #{aid} était déjà traitée : <b>{html.escape(r['status'])}</b> par {par}, le {_date(r['decided'])}. "
                "Aucune nouvelle notification n'a été envoyée à Claude.</div>")
    if res == "expiree":
        return (f"<div class='resultat ko'>La demande #{aid} a expiré (délai de 24 h) avant ta décision. "
                "Aucune notification d'approbation ou de refus n'a été envoyée.</div>")
    return f"<div class='resultat ko'>Tu ne peux pas trancher la demande #{aid} : décision réservée au propriétaire.</div>"

async def ui(req: Request):
    role = human_role(req)
    if not role:
        return Response("Authentification requise", status_code=401, headers={"WWW-Authenticate": 'Basic realm="IA-partag"'})
    ags, msgs, _, unread = state(); e = html.escape
    q = dict(req.query_params)
    msgs = [m for m in msgs if _garde_msg(m, q)]
    with db() as c:
        cmax = c.execute("SELECT COALESCE(MAX(id),0) m FROM messages").fetchone()["m"]
        emax = c.execute("SELECT COALESCE(MAX(id),0) m FROM events").fetchone()["m"]
        ap = c.execute("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM approvals WHERE status='en attente'").fetchone()
    rows = "".join(f"<tr><td>{e(m['created'])}</td><td>{e(m['sender'])}→{e(m['recipient'])}</td><td><a href='{e(url_with(q, fil=m['thread']))}'>{e(m['thread'])}</a></td><td>{'Récupérée' if m['delivered'] else 'À récupérer'}</td><td>{e(m['priority'])}</td><td>{e(m['body'][:200])}</td></tr>" for m in msgs)
    entete = (f"<header class=top><div><h2>IA-partag — suivi</h2><span class=meta>Connecté : <b>{e(_libelle_role(role))}</b></span></div>"
              f"<div class=meta>Dernière actualisation : <span id=heure-maj>{now()[11:]}</span> · <span id=statut-maj>Actualisation automatique</span><br>"
              "<button id=bouton-pause class='bouton b-gris' type=button aria-pressed=false>Pause</button> "
              "<button id=bouton-densite class='bouton b-gris' type=button>Densité : normale</button></div></header>"
              f"<div id=etat data-messages={cmax} data-evenements={emax} data-enattente={ap['n']} data-derniere={ap['m']}></div>"
              "<div id=banniere class=banniere role=status><span id=banniere-texte>Nouveautés disponibles.</span> "
              "<button class='bouton b-bleu' type=button onclick='iapartagRecharger()'>Afficher</button></div>")
    fil = q.get("fil", "")[:100]
    appr = approvals_block(role) + ((pairing_block() + invite_block()) if role == "owner" else "")
    corps_fil = (f"<div class=fils-grille><div id=liste-fils>{fils_block(q)}</div>"
                 f"<div id=zone-detail><div id=fil-detail>{fil_detail(q, fil)}</div></div></div>")
    body = (f"{entete}{res_banner(q)}<div class=cards>{cartes_block(ags, unread)}</div>{appr}"
            f"<h3>Fils et messages</h3>{filtres_bar(q)}{corps_fil}"
            f"<details open data-memo=messages><summary>Messages (30 derniers)</summary><div class=table-wrap><table><tr><th>date<th>sens<th>fil<th>état<th>prio<th>texte</tr>{rows}</table></div></details>"
            f"<details open data-memo=memoire><summary>Mémoire partagée (lecture seule)</summary>{memory_block(q)}</details>"
            f"<details open data-memo=journal><summary>Journal et sauvegardes</summary>{journal_block(q)}</details>"
            f"<small>Affichage : {now()}</small>")
    return HTMLResponse(_page(body))

def blocked_page(exc):
    user = html.escape(exc.user)
    return HTMLResponse(_page(f"<h2>Connexion bloquée</h2><div class='resultat ko'>Trop d'identifiants invalides depuis ce client (nom saisi : <b>{user}</b>). "
                               f"Motif : {HUM_MAX_FAILS} échecs en {HUM_WINDOW_MIN} minutes. Réessaie dans <b>{exc.minutes} min</b>.<br>"
                               "Cette limite ne concerne que ce client : l'autre utilisateur n'est pas bloqué.</div>", "Connexion bloquée"),
                        status_code=429, headers={"Retry-After": str(exc.minutes * 60)})

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

def _peut_trancher(role, r):
    return role == "owner" or (r["requester"] == "A" and (r["level"] or "local") == "local")

async def approval_decide(req: Request):
    role = human_role(req)
    if not role: return Response("Authentification requise", status_code=401)
    f = await form_data(req)
    try: aid = int(f.get("id", ""))
    except Exception: return Response("id invalide", status_code=400)
    if not hmac.compare_digest(str(f.get("csrf", "")), csrf(aid)): return Response("jeton de formulaire invalide", status_code=403)
    dec = {"approve": "approuvée", "deny": "refusée"}.get(f.get("decision"))
    if not dec: return Response("décision invalide", status_code=400)
    note = str(f.get("note", ""))[:300]
    par = "le propriétaire" if role == "owner" else "l'humain du PC 2"
    with db() as c:
        expire(c)
        r = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if not r: return RedirectResponse(f"/ui?res=introuvable&id={aid}", status_code=303)
        if not _peut_trancher(role, r):
            event("validation-refusée", f"{role} n'a pas le droit de trancher #{aid} ({r['requester']}, {r['level']})", c)
            return RedirectResponse(f"/ui?res=interdit&id={aid}", status_code=303)
        # Mise à jour conditionnelle : une seule décision possible, même si deux humains valident en même temps.
        cur = c.execute("UPDATE approvals SET status=?, decided=?, note=?, decided_by=? WHERE id=? AND status='en attente'", (dec, now(), note, par, aid))
        if cur.rowcount != 1:
            s = c.execute("SELECT status FROM approvals WHERE id=?", (aid,)).fetchone()["status"]
            return RedirectResponse(f"/ui?res={'expiree' if s == 'expirée' else 'deja'}&id={aid}", status_code=303)
        body = (f"[VALIDATION #{aid}] Décision de {par} : {dec.upper()}. Action concernée : {r['action']}." + (f" Note : {note}" if note else "")
                + (" Tu peux l'exécuter, uniquement cette action." if dec == "approuvée" else " Ne l'exécute pas."))
        c.execute("INSERT INTO messages(uid,thread,sender,recipient,priority,body,created) VALUES(?,?,?,?,?,?,?)",
                  (None, f"validation-{aid}", "H", r["requester"], "urgente", body, now()))
        event("validation", f"#{aid} {dec} par {par} pour {r['requester']}", c)
    return RedirectResponse(f"/ui?res=ok&id={aid}", status_code=303)

def _echeance(created):
    fin = datetime.datetime.fromisoformat(created) + datetime.timedelta(hours=APPROVAL_TTL_H)
    reste = int((fin - datetime.datetime.now()).total_seconds() // 60)
    if reste <= 0: return f"échéance {fin:%d/%m %H:%M} (dépassée)"
    return f"échéance {fin:%d/%m %H:%M} (reste {reste // 60} h {reste % 60:02d})"

def approvals_block(role="owner"):
    e = html.escape
    with db() as c:
        expire(c)
        pend = c.execute("SELECT * FROM approvals WHERE status='en attente' ORDER BY id").fetchall()
        hist = c.execute("SELECT * FROM approvals WHERE status!='en attente' ORDER BY id DESC LIMIT 15").fetchall()
    # Bloc « À traiter » : ce que le rôle connecté peut trancher, les plus proches de l'échéance d'abord
    a_traiter = [a for a in pend if _peut_trancher(role, a)]
    out = "<div class='bloc urgent' id=a-traiter><h3 style='margin-top:0'>À traiter</h3>"
    if not a_traiter: out += "<p>Aucune décision attendue de toi.</p>"
    out += "".join(f"<p>#{a['id']} — Claude {e(a['requester'])} : {e(a['action'])} <small>({_echeance(a['created'])})</small></p>" for a in a_traiter)
    out += "</div><h3>Demandes de validation en attente</h3>"
    if not pend: out += "<p>Aucune demande en attente.</p>"
    for a in pend:
        lvl = a["level"] or "local"
        can = _peut_trancher(role, a)
        tag = "<span class='etiq critique'>CRITIQUE</span>" if lvl == "critique" else "<span class=etiq>local</span>"
        out += (f"<div class='bloc' style='margin:8px 0'><b>#{a['id']} — Claude {e(a['requester'])}</b> {tag} <small>({e(a['created'])}, {_echeance(a['created'])})</small><br>"
                f"<b>Action :</b> {e(a['action'])}<br><small>{e(a['details'])}</small>")
        if can:
            out += (f"<form method=post action='/ui/decision' style='margin-top:8px'><input type=hidden name=id value={a['id']}>"
                    f"<input type=hidden name=csrf value={csrf(a['id'])}>"
                    f"<label for=note-{a['id']}>Note (facultative)</label> <input class=note id=note-{a['id']} data-id={a['id']} name=note maxlength=300 placeholder='note (facultatif)'> "
                    f"<span class=compteur id=compteur-{a['id']}>0 / 300</span><br>"
                    f"<button class='bouton b-ok' name=decision value=approve>Approuver</button> "
                    f"<button class='bouton b-ko' name=decision value=deny>Refuser</button></form></div>")
        else:
            out += "<p><i>Décision réservée au propriétaire.</i></p></div>"
    rows = "".join(f"<tr><td>#{a['id']}</td><td>{e(a['requester'])}</td><td>{e(a['level'] or 'local')}</td><td>{e(a['action'])}</td><td>{e(a['status'])}</td><td>{e(a['decided_by'] or 'non renseigné')}</td><td>{e(a['decided'] or '')}</td><td>{e(a['note'] or '')}</td></tr>" for a in hist)
    out += ("<h3>Historique des validations</h3><div class=table-wrap><table><tr><th>n°<th>de<th>niveau<th>action<th>décision<th>décidée par<th>date<th>note</tr>"
            + (rows or "<tr><td colspan=8>aucune</td></tr>") + "</table></div>")
    return out

PAIR_TTL_MIN = 10          # validité d'un code d'appairage
PAIR_MAX_FAILS = 5         # échecs max par fenêtre de 10 min, tous clients confondus
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

def hash_code(code):
    return hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()

async def pair_create(req: Request):
    if not is_admin(req): return Response("Authentification requise", status_code=401)
    f = await form_data(req); agent = f.get("agent")
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

def _etat_code(r, t):
    if r is None: return "aucun code généré"
    if not r["used"] and r["expires"] >= t:
        return f"actif, créé à {r['created'][11:16]}, expire à {r['expires'][11:16]}"
    return f"créé à {r['created'][11:16]} : expiré, ou déjà utilisé ou remplacé"

def pairing_block():
    e = html.escape
    t = now()
    with db() as c:
        dern = {a: c.execute("SELECT * FROM pairings WHERE agent=? ORDER BY id DESC LIMIT 1", (a,)).fetchone() for a in AGENTS}
    lignes, forms = "", ""
    for a in AGENTS:
        r = dern[a]
        actif = bool(r and not r["used"] and r["expires"] >= t)
        lignes += f"<li>Code pour le Claude {a} : {e(_etat_code(r, t))}</li>"
        libelle = f"Remplacer le code pour le Claude {a}" if actif else f"Générer un code pour le Claude {a}"
        confirm = " onsubmit=\"return confirm('Le code actuel ne sera plus valable. Continuer ?')\"" if actif else ""
        forms += (f"<form method=post action='/ui/pair-create' style='display:inline-block;margin-right:10px'{confirm}>"
                  f"<input type=hidden name=agent value={a}><input type=hidden name=csrf value={csrf('pair-' + a)}>"
                  f"<button class='bouton b-bleu'>{libelle}</button></form>")
    return (f"<h3>Appairer un PC (sans clé USB)</h3>"
            f"<p><small>Commande réservée au propriétaire. Code à usage unique, valable {PAIR_TTL_MIN} min, à donner au Claude de l'autre PC.<br>"
            "Effet : le PC qui saisit le code reçoit le jeton du Claude choisi. Le compte de l'humain du PC 2 n'est pas modifié.</small></p>"
            f"<ul>{lignes}</ul>{forms}")

INVITE_TTL_MIN = 10

def migrate():
    with db() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(approvals)")]
        if "level" not in cols: c.execute("ALTER TABLE approvals ADD COLUMN level TEXT DEFAULT 'local'")
        if "decided_by" not in cols: c.execute("ALTER TABLE approvals ADD COLUMN decided_by TEXT")
        c.executescript("""CREATE TABLE IF NOT EXISTS humans(name TEXT PRIMARY KEY, salt TEXT, hash TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS invites(id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT, created TEXT, expires TEXT, used INTEGER DEFAULT 0);""")

def invite_block():
    e = html.escape
    t = now()
    with db() as c:
        inv = c.execute("SELECT * FROM invites ORDER BY id DESC LIMIT 1").fetchone()
        hum = c.execute("SELECT created FROM humans WHERE name='humain-A'").fetchone()
    actif = bool(inv and not inv["used"] and inv["expires"] >= t)
    etat = "aucun code généré" if inv is None else _etat_code(inv, t)
    if hum:
        effet = f"le compte humain-A existe depuis le {e(hum['created'])} : valider le code <b>remplacera son mot de passe</b>."
    else:
        effet = "aucun compte humain-A pour l'instant : valider le code <b>créera le compte humain-A</b>."
    libelle = "Remplacer le code" if actif else "Générer un code d'invitation"
    confirm = " onsubmit=\"return confirm('Le code actuel ne sera plus valable. Continuer ?')\"" if actif else ""
    return (f"<h3>Inviter l'humain du PC 2</h3>"
            f"<p><small>Commande réservée au propriétaire. Destinataire : humain du PC 2 (identifiant <b>humain-A</b>). "
            f"Code à usage unique, valable {INVITE_TTL_MIN} min. La personne ouvre http://{e(CFG['host'])}:{CFG['port']}/invite et choisit elle-même son mot de passe.</small></p>"
            f"<p>Dernier code : {e(etat)}.<br>Effet à la validation : {effet}</p>"
            f"<form method=post action='/ui/invite-create'{confirm}><input type=hidden name=csrf value={csrf('invite')}>"
            f"<button class='bouton' style='background:#8957e5'>{libelle}</button></form>")

async def invite_create(req: Request):
    if not is_admin(req): return Response("Réservé au propriétaire", status_code=401)
    f = await form_data(req)
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
    f = await form_data(req); pw, pw2 = str(f.get("pw", "")), str(f.get("pw2", ""))
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

def _like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def memory_block(q):
    e = html.escape
    mem = q.get("mem", "")[:100]
    cle = q.get("cle", "")[:100]
    with db() as c:
        if mem:
            rows = c.execute("SELECT * FROM memory WHERE key LIKE ? ESCAPE '\\' ORDER BY key", (f"%{_like(mem)}%",)).fetchall()
        else:
            rows = c.execute("SELECT * FROM memory ORDER BY key").fetchall()
        histo = c.execute("SELECT version, author, ts FROM memory_history WHERE key=? ORDER BY version", (cle,)).fetchall() if cle else []
    body = "".join(
        f"<tr><td>{e(r['key'])}</td><td>v{r['version']}</td><td>{e(r['author'])}</td><td>{e(r['updated'])}</td>"
        f"<td><pre class=valeur>{e(r['value'])}</pre></td></tr>" for r in rows)
    recherche = (f"<form method=get action=/ui><label for=mem>Chercher une clé</label> "
                 f"<input class=cherche id=mem name=mem value='{e(mem)}' maxlength=100> <button class='bouton b-gris'>Chercher</button>"
                 + (f" <a href='{e(url_with(q, mem=None))}'>Effacer la recherche</a>" if mem else "") + "</form>")
    comparaison = (f"<form method=get action=/ui style='margin-top:10px'><label for=cle>Comparer deux versions d'une clé</label> "
                   f"<input class=cherche id=cle name=cle value='{e(cle)}' maxlength=100 style='max-width:200px'> "
                   "v <input name=v1 type=number min=1 style='width:70px'> → v <input name=v2 type=number min=1 style='width:70px'> "
                   "<button class='bouton b-gris'>Comparer</button></form>")
    res = ""
    if cle:
        vers = ", ".join(f"v{h['version']} ({e(h['author'])}, {e(h['ts'])})" for h in histo) or "aucune version historisée"
        res += f"<p><small>Versions de {e(cle)} : {vers}</small></p>"
        if q.get("v1") and q.get("v2"):
            try:
                v1, v2 = int(q["v1"]), int(q["v2"])
                with db() as c:
                    a = c.execute("SELECT value FROM memory_history WHERE key=? AND version=?", (cle, v1)).fetchone()
                    b = c.execute("SELECT value FROM memory_history WHERE key=? AND version=?", (cle, v2)).fetchone()
            except ValueError:
                a = b = None
            if a and b:
                lignes = []
                for l in difflib.unified_diff(a["value"].splitlines(), b["value"].splitlines(), f"v{v1}", f"v{v2}", lineterm=""):
                    if l.startswith("+++") or l.startswith("---") or l.startswith("@@"): lignes.append(f"<small>{e(l)}</small>")
                    elif l.startswith("+"): lignes.append(f"<span class=diff-plus>{e(l)}</span>")
                    elif l.startswith("-"): lignes.append(f"<span class=diff-moins>{e(l)}</span>")
                    else: lignes.append(e(l))
                res += "<pre class=valeur>" + ("\n".join(lignes) or "Aucune différence de contenu entre ces deux versions.") + "</pre>"
            else:
                res += "<p>Version introuvable pour cette clé.</p>"
    return ("<h3>Mémoire partagée (lecture seule)</h3>" + recherche +
            "<div class=table-wrap><table><tr><th>clé<th>version<th>auteur<th>maj<th>valeur complète</tr>"
            + (body or "<tr><td colspan=5>aucune clé ne correspond</td></tr>") + "</table></div>" + comparaison + res)

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
        except Exception as ex:
            logging.error("sauvegarde échouée : %s", ex)
            try: event("sauvegarde-échec", f"la copie automatique a échoué : {str(ex)[:200]}")
            except Exception: pass
        time.sleep(BACKUP_EVERY_H * 3600)

async def backup_latest(req: Request):
    """Derniere sauvegarde de la base, reservee a l'agent A (PC 2) : copie hors du PC 1."""
    if agent_of(req) != "A": return JSONResponse({"erreur": "non autorisé"}, status_code=401)
    files = sorted(BACKUP_DIR.glob("bus-*.db"))
    if not files: return JSONResponse({"erreur": "aucune sauvegarde"}, status_code=404)
    f = files[-1]
    with db() as c: event("sauvegarde", f"copie {f.name} envoyée au PC 2", c)
    return Response(f.read_bytes(), media_type="application/octet-stream", headers={"X-Backup-Name": f.name})

init()
migrate()
mem_init()
threading.Thread(target=backup_loop, daemon=True).start()
app = Starlette(routes=[Route("/send", send, methods=["POST"]), Route("/inbox", inbox), Route("/heartbeat", heartbeat, methods=["POST"]),
                        Route("/status", status), Route("/backup/latest", backup_latest), Route("/approval", approval_new, methods=["POST"]), Route("/memory", memory_get), Route("/memory/put", memory_put, methods=["POST"]), Route("/approval/{aid:int}", approval_get), Route("/ui/decision", approval_decide, methods=["POST"]), Route("/ui/pair-create", pair_create, methods=["POST"]), Route("/ui/invite-create", invite_create, methods=["POST"]), Route("/ui/etat", ui_state), Route("/invite", invite, methods=["GET", "POST"]), Route("/pair", pair, methods=["POST"]), Route("/ui", ui), Route("/", ui)],
                exception_handlers={HumanBlocked: lambda req, exc: blocked_page(exc)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG["host"], port=CFG["port"], log_level="warning")
