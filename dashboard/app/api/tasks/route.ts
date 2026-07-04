import { NextResponse } from "next/server";
import { getTasks } from "@/lib/replay";

// Tasks feed polled fast by the live task board. Demo mode: the final stretch of
// real task lifecycles replays on a loop (lib/replay.ts) so the board keeps moving.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  return NextResponse.json(getTasks());
}
