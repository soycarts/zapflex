"""Human side of the approval gate: list and resolve pending approvals.

The dashboard is display-only, so this is how the founder taps approve/reject. A
future Wassist/Telegram inbound webhook would call gate.resolve() the same way.

Usage:
    python -m agents.approve list
    python -m agents.approve approve <id>
    python -m agents.approve reject  <id>
"""
from __future__ import annotations

import sys

from agents import db, gate


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    conn = db.connect()
    cmd = sys.argv[1]

    if cmd == "list":
        rows = db.fetchall(
            conn,
            """select id, requested_by, action_type, status, payload, created_at
               from pending_approvals where status='pending' order by id""",
        )
        if not rows:
            print("no pending approvals")
            return
        for r in rows:
            print(f"#{r['id']:>3} [{r['action_type']}] by {r['requested_by']}: {r['payload']}")
        return

    if cmd in ("approve", "reject") and len(sys.argv) == 3:
        approval_id = int(sys.argv[2])
        gate.resolve(conn, approval_id, cmd, by="carter")
        print(f"approval #{approval_id} -> {'approved' if cmd=='approve' else 'rejected'}")
        return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
