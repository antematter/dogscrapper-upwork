import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function GET() {
  try {
    const res = await fetch(`${API_URL}/scrape/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: "dog_beds", top_n: 2 }),
    });
    if (!res.ok) throw new Error(`Backend returned ${res.status}`);
    return NextResponse.redirect(new URL("/", "http://localhost:3000"));
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
