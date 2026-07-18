# -*- coding: utf-8 -*-
"""OPS WAR-ROOM ALERTS for paid orders not yet dispatched, by age:
  Tier 1 (>=2 business days): reminder — risk of upset, War Room with Chris if not out by day 3.
  Tier 2 (>=4 days): OVERDUE — organize War Room now with justifications.
Lists the actual Shopify order IDs. DRY-RUN by default (prints the messages); pass --send to post to Slack
(requires the bot to have chat:write + be in the channel). Schedule via cron for a daily run."""
import sqlite3, io, sys
from datetime import date, timedelta
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

ALERT_CHANNEL = "C0AQ8DD3GJF"   # #petproject-whatsapp
TAG = "<@U0AQHCCC5PE> <@U0AR82XJVFS>"   # Ae Perez, Cloddy Mae Ritual
TEST = "--test" in sys.argv            # send live but with NO tags + a test banner
SEND = ("--send" in sys.argv) or TEST
if TEST:
    TAG = ""
TODAY = date.today()

def business_days(start):
    d, cur = 0, start
    while cur < TODAY:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            d += 1
    return d

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
orders = cur.execute("SELECT order_name, created, name FROM clientssot_unfulfilled_orders WHERE created!=''").fetchall()
con.close()

reminder, overdue = [], []
for o in orders:
    try:
        bd = business_days(date.fromisoformat(o["created"]))
        cal = (TODAY - date.fromisoformat(o["created"])).days
    except Exception:
        continue
    who = o["name"] or "?"
    if cal >= 4:
        overdue.append(f'{o["order_name"]} ({who}) — *{cal} days* elapsed')
    elif bd >= 2:
        reminder.append(f'{o["order_name"]} ({who}) — *{bd} business days* elapsed')

def fmt(ids):
    return "\n".join(f"• {x}" for x in ids)

msgs = []
if reminder:
    msgs.append(("REMINDER (2 business days)",
        "⏰ *The following orders have elapsed for 2 days:*\n" + fmt(reminder) +
        "\n\nCustomer Satisfaction is key to our success. These customers risk being upset. "
        "If they are not dispatched by the 3rd day, host a War Room meeting with Chris and explain "
        "what is blocking delivery.\n\n" + TAG))
if overdue:
    msgs.append(("OVERDUE (4+ days)",
        "🚨 *These orders are OVERDUE.* Please organize War Room meeting now, with justifications on why "
        "they have not yet dispatched.\n" + fmt(overdue) + "\n\n" + TAG))

if not msgs:
    print("No orders at the 2-day or 4-day thresholds. Nothing to send."); sys.exit(0)

for tier, text in msgs:
    if TEST:
        text = "🧪 *TEST — please ignore* (Rosetta IMS ops alert dry-run)\n\n" + text
    print(f"\n===== {tier} =====\n{text}\n")
    if SEND:
        r = requests.post("https://slack.com/api/chat.postMessage",
                          headers={"Authorization": f"Bearer {cfg['SLACK_TOKEN']}", "Content-Type": "application/json"},
                          json={"channel": ALERT_CHANNEL, "text": text}, timeout=30)
        print("  -> posted ok:", r.json().get("ok"), "| error:", r.json().get("error"))
        if TEST:
            break   # one test message is enough
if not SEND:
    print("(DRY RUN — no messages sent. Re-run with --send once chat:write is enabled + you approve.)")
