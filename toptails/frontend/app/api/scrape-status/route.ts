import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const emptyPayload = {
  scrape_running: false,
  job_started_at: null,
  last_finished_at: null,
  last_saved_rows: null,
  last_error: null,
  last_scrape_debug: null,
  active_target: null,
};

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/scrape/status`, { cache: "no-store" });
    if (!res.ok) {
      return NextResponse.json(emptyPayload);
    }
    const data = (await res.json()) as {
      scrape_running?: boolean;
      job_started_at?: string | null;
      last_finished_at?: string | null;
      last_saved_rows?: number | null;
      last_error?: string | null;
      last_scrape_debug?: Record<string, unknown> | null;
      active_target?: string | null;
    };
    return NextResponse.json({
      scrape_running: Boolean(data.scrape_running),
      active_target: data.active_target ?? null,
      job_started_at: data.job_started_at ?? null,
      last_finished_at: data.last_finished_at ?? null,
      last_saved_rows:
        typeof data.last_saved_rows === "number" ? data.last_saved_rows : null,
      last_error: data.last_error ?? null,
      last_scrape_debug:
        data.last_scrape_debug && typeof data.last_scrape_debug === "object"
          ? data.last_scrape_debug
          : null,
    });
  } catch {
    return NextResponse.json(emptyPayload);
  }
}
