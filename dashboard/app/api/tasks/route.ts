import { NextResponse } from "next/server";
import { q } from "@/lib/db";

// Dedicated, lightweight tasks feed polled fast by the live task board. Ordered by
// most-recent activity so freshly created and just-completed tasks bubble to the top.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    const tasks = await q(
      `select id, title, phase, category, status, priority,
              created_by_type, created_by_name, assigned_to,
              completed_by_type, completed_by_name,
              created_at, started_at, completed_at
       from tasks
       order by coalesce(completed_at, started_at, created_at) desc, id desc
       limit 80`
    );
    return NextResponse.json({ tasks, ts: Date.now() });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
