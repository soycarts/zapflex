import { NextResponse } from "next/server";
import { getSnapshot } from "@/lib/replay";

// Demo mode: the swarm's final state replays from bundled fixtures (lib/replay.ts).
// Still dynamic — timestamps rebase onto the replay loop on every poll.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  return NextResponse.json(getSnapshot());
}
