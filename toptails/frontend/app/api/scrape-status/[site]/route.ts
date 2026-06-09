import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const VALID_SITES = new Set([
  "amazon",
  "walmart",
  "chewy",
  "petsmart",
  "petco",
  "target",
  "tractor_supply",
]);

export async function GET(
  _request: Request,
  context: { params: Promise<{ site: string }> }
) {
  const { site } = await context.params;
  if (!VALID_SITES.has(site)) {
    return NextResponse.json({ error: `Unknown site: ${site}` }, { status: 404 });
  }

  try {
    const res = await fetch(`${API_URL}/scrape/status/${site}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json(
        {
          site,
          scrape_running: false,
          global_scrape_running: false,
          active_target: null,
          job_started_at: null,
          last_finished_at: null,
          last_saved_rows: null,
          last_error: null,
          debug: null,
        },
        { status: 200 }
      );
    }
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({
      site,
      scrape_running: false,
      global_scrape_running: false,
      active_target: null,
      job_started_at: null,
      last_finished_at: null,
      last_saved_rows: null,
      last_error: null,
      debug: null,
    });
  }
}
