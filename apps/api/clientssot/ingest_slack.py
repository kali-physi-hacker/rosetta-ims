# -*- coding: utf-8 -*-
"""FULL operational CS sync: read the WhatsApp-mirror Slack channels, extract every inbound conversation
(posted as 'Name(phone)' by the bridge bot), aggregate per phone, match to the SSOT by phone.
Token from .env SLACK_TOKEN (rotating user token ~12h; for a permanent sync use a bot token + refresh)."""
import sqlite3, io, sys, re, time
from datetime import datetime, timezone
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
TOK = cfg["SLACK_TOKEN"]
HDR = {"Authorization": f"Bearer {TOK}"}
CHANNELS = {"C0AQ8DD3GJF": "PetProject", "C0B8EB6HAG5": "Ohana"}
# whole message text is "<optional name>(<phone>)" -> an inbound WhatsApp conversation (excludes long reports)
PAT = re.compile(r'^(.*?)\(\+?(\d{8,15})\)\s*$')

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

def history(channel):
    cursor = None
    while True:
        params = {"channel": channel, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        for attempt in range(6):
            r = requests.get("https://slack.com/api/conversations.history", headers=HDR, params=params, timeout=40)
            j = r.json()
            if j.get("ok"):
                break
            if j.get("error") == "ratelimited":
                time.sleep(int(r.headers.get("Retry-After", 5))); continue
            print("  slack error:", j.get("error")); return
        for m in j.get("messages", []):
            yield m
        cursor = (j.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return
        time.sleep(1.2)

# aggregate per phone: latest contact, count of reach-outs, a display name, channel
agg = {}   # phone8 -> {name, channel, last_ts, count, raw_phone}
for ch_id, ch_name in CHANNELS.items():
    n = 0
    for m in history(ch_id):
        txt = (m.get("text") or "").strip()
        mt = PAT.match(txt)
        if not mt:
            continue
        name, phone = mt.group(1).strip(), mt.group(2)
        p8 = norm_phone(phone)
        if not p8:
            continue
        n += 1
        ts = float(m.get("ts", 0))
        e = agg.setdefault(p8, {"name": "", "channel": ch_name, "last_ts": 0, "count": 0, "raw": phone})
        e["count"] += 1
        if name and not e["name"]:
            e["name"] = name
        if ts > e["last_ts"]:
            e["last_ts"] = ts; e["channel"] = ch_name
    print(f"{ch_name}: {n} conversation messages", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_cs_contacts(
    phone TEXT PRIMARY KEY, name TEXT, channel TEXT, last_contact TEXT, msg_count INT, customer_id TEXT)""")
cur.execute("DELETE FROM clientssot_cs_contacts")
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}

matched = 0
for p8, e in agg.items():
    cid = phone2cust.get(p8)
    if cid:
        matched += 1
    last = datetime.fromtimestamp(e["last_ts"], timezone.utc).strftime("%Y-%m-%d") if e["last_ts"] else ""
    cur.execute("INSERT OR REPLACE INTO clientssot_cs_contacts VALUES (?,?,?,?,?,?)",
                (e["raw"], e["name"], e["channel"], last, e["count"], cid))
con.commit()
print(f"\ndistinct phones {len(agg)} | matched to SSOT {matched}")
con.close()
print("SLACK CS SYNC DONE")
