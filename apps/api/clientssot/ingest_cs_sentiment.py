# -*- coding: utf-8 -*-
"""OPS sentiment: read each WhatsApp/CS conversation's thread replies and classify the customer's state
as poor (complaint) / happy / fine. Keyword-based v1 (EN + some zh-HK) — good enough to triage; can be
upgraded to LLM classification later. Stores the LATEST conversation's sentiment per phone in cs_contacts."""
import sqlite3, io, sys, re, time
from pathlib import Path
import requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()
ENV = Path(__file__).resolve().parents[2] / ".env"
cfg = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
HDR = {"Authorization": f"Bearer {cfg['SLACK_TOKEN']}"}
CHANNELS = {"C0AQ8DD3GJF": "PetProject", "C0B8EB6HAG5": "Ohana"}
PAT = re.compile(r'^(.*?)\(\+?(\d{8,15})\)\s*$')

POOR = ["refund", "complaint", "complain", "angry", "lawyer", "terrible", "wrong", "broken", "damaged",
        "delay", "late", "disappoint", "unacceptable", "cancel", "missing", "worst", "scam", "not happy",
        "投訴", "退款", "退貨", "好慢", "壞", "唔見", "點解", "冇人"]
HAPPY = ["thank", "thanks", "thx", "great", "perfect", "love it", "awesome", "excellent", "appreciate",
         "helpful", "happy", "多謝", "唔該晒", "感謝", "好正", "讚", "滿意", "👍", "❤"]

def _snippet(text, w):
    i = text.lower().find(w)
    if i < 0:
        return ""
    s = max(0, i - 70); e = min(len(text), i + len(w) + 70)
    return ("…" if s > 0 else "") + text[s:e].strip().replace("\n", " ") + ("…" if e < len(text) else "")

def classify(text):
    """Return (sentiment, evidence_quote) — cite the specific line that drove the judgment (per Seph call)."""
    t = text.lower()
    for w in POOR:
        if w in t:
            return "poor", _snippet(text, w)
    for w in HAPPY:
        if w in t:
            return "happy", _snippet(text, w)
    return "fine", ""

def api(method, params):
    for attempt in range(6):
        r = requests.get(f"https://slack.com/api/{method}", headers=HDR, params=params, timeout=40)
        j = r.json()
        if j.get("ok"):
            return j
        if j.get("error") == "ratelimited":
            time.sleep(int(r.headers.get("Retry-After", 5))); continue
        return j
    return {}

def norm_phone(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
for coldef in ("sentiment TEXT", "sentiment_quote TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_cs_contacts ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
con.commit(); con.close()

latest = {}   # phone8 -> (ts, sentiment, quote)
for ch_id in CHANNELS:
    cursor = None; n = 0
    while True:
        p = {"channel": ch_id, "limit": 200}
        if cursor: p["cursor"] = cursor
        j = api("conversations.history", p)
        if not j.get("ok"):
            print("hist error", j.get("error")); break
        for m in j.get("messages", []):
            mt = PAT.match((m.get("text") or "").strip())
            if not mt:
                continue
            p8 = norm_phone(mt.group(2)); ts = float(m.get("ts", 0))
            if not p8 or (p8 in latest and latest[p8][0] >= ts):
                continue
            text = m.get("text") or ""
            if m.get("reply_count"):
                rep = api("conversations.replies", {"channel": ch_id, "ts": m["ts"], "limit": 50})
                text += " " + " ".join(x.get("text", "") for x in rep.get("messages", []))
                time.sleep(0.5)
            lab, quote = classify(text)
            latest[p8] = (ts, lab, quote); n += 1
        cursor = (j.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(1.2)
    print(f"{CHANNELS[ch_id]}: {n} conversations classified", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
# cs_contacts keyed by raw phone; match on last-8
rows = cur.execute("SELECT phone FROM clientssot_cs_contacts").fetchall()
upd = 0
for (raw,) in rows:
    s = latest.get(norm_phone(raw))
    if s:
        cur.execute("UPDATE clientssot_cs_contacts SET sentiment=?, sentiment_quote=? WHERE phone=?", (s[1], s[2], raw)); upd += 1
con.commit()
from collections import Counter
dist = Counter(lab for _, lab, _ in latest.values())
print(f"\nclassified phones {len(latest)} | cs rows updated {upd} | dist {dict(dist)}")
con.close()
print("CS SENTIMENT INGEST DONE")
