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

export async function POST(
  _request: Request,
  context: { params: Promise<{ site: string }> }
) {
  const { site } = await context.params;
  if (!VALID_SITES.has(site)) {
    return NextResponse.json({ error: `Unknown site: ${site}` }, { status: 404 });
  }

  try {
    const res = await fetch(`${API_URL}/scrape/run/${site}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: "dog_beds", top_n: 2 }),
    });
    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json(
        { error: text || `Backend returned ${res.status}` },
        { status: res.status }
      );
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
