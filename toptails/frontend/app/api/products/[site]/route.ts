import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

const ALLOWED_SITES = new Set([
  "chewy",
  "amazon",
  "walmart",
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

  if (!ALLOWED_SITES.has(site)) {
    return NextResponse.json({ error: "Unknown site" }, { status: 400 });
  }

  try {
    const res = await fetch(
      `${API_URL}/products?category=dog_beds&top_n=2`,
      { cache: "no-store" }
    );

    if (!res.ok) {
      return NextResponse.json(
        { error: `Backend returned ${res.status}` },
        { status: res.status }
      );
    }

    const data = (await res.json()) as {
      results?: Array<{
        site: string;
        scrape_status: string;
        scrape_notes: string | null;
        top_products: unknown[];
      }>;
    };

    const siteResult = data.results?.find((r) => r.site === site);
    if (!siteResult) {
      return NextResponse.json({
        site,
        scrape_status: "ok",
        scrape_notes: null,
        top_products: [],
      });
    }

    return NextResponse.json(siteResult);
  } catch (e) {
    return NextResponse.json(
      { error: String(e) },
      { status: 502 }
    );
  }
}
