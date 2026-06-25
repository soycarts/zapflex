#!/usr/bin/env python3
"""task.py - shared task tracker over the Supabase tasks table."""
import os, sys, argparse, datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()

# Actor types tracked on the tasks board (drives the dashboard timeline colours):
#   human (carter), claude_code, cursor (Cursor / Opus 4.8), agent (the run-time swarm).
def actor_type(by):
    return "human" if by in ("carter", "human") else by

def add(a):
    t = sb.table("tasks").insert({
        "title": a.title, "phase": a.phase, "category": a.category,
        "created_by_type": actor_type(a.by),
        "created_by_name": a.by, "assigned_to": a.to, "status": "todo",
    }).execute()
    print("created", t.data[0]["id"], "-", a.title)

def ls(a):
    q = sb.table("tasks").select("*").order("created_at")
    if a.phase: q = q.eq("phase", a.phase)
    if a.status: q = q.eq("status", a.status)
    for t in q.execute().data:
        m = {"todo": "[ ]", "doing": "[~]", "done": "[x]"}.get(t["status"], "[ ]")
        print(f'{m} #{t["id"]:>3} ({t["phase"]}/{t["category"]}) {t["title"]}')

def start(a):
    sb.table("tasks").update({"status": "doing", "started_at": now()}).eq("id", a.id).execute()
    print("started", a.id)

def done(a):
    upd = {"status": "done", "completed_at": now(),
           "completed_by_type": actor_type(a.by),
           "completed_by_name": a.by}
    if a.note: upd["result"] = a.note
    if a.ref:  upd["source_ref"] = a.ref
    sb.table("tasks").update(upd).eq("id", a.id).execute()
    print("done", a.id)

p = argparse.ArgumentParser(); s = p.add_subparsers(required=True)
a_ = s.add_parser("add"); a_.add_argument("title"); a_.add_argument("--phase", default="prep")
a_.add_argument("--category", default="general"); a_.add_argument("--by", default="carter")
a_.add_argument("--to", default=None); a_.set_defaults(fn=add)
l_ = s.add_parser("list"); l_.add_argument("--phase"); l_.add_argument("--status"); l_.set_defaults(fn=ls)
st = s.add_parser("start"); st.add_argument("id", type=int); st.set_defaults(fn=start)
dn = s.add_parser("done"); dn.add_argument("id", type=int); dn.add_argument("--by", default="claude_code")
dn.add_argument("--note"); dn.add_argument("--ref"); dn.set_defaults(fn=done)
args = p.parse_args(); args.fn(args)
