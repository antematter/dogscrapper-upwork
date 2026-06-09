"use client";

import { useEffect, useState } from "react";

type SiteDebug = {
  scraper_returned_rows?: number;
  ok_product_rows?: number;
  blocked?: boolean;
  scrape_notes?: string | null;
  rows_queued_for_db?: number;
};

type ScrapeDebug = {
  top_n?: number;
  sites?: Record<string, SiteDebug>;
};

const SITE_LABELS: Record<string, string> = {
  chewy: "Chewy",
  amazon: "Amazon",
  walmart: "Walmart",
  petsmart: "PetSmart",
  petco: "Petco",
  target: "Target",
  tractor_supply: "Tractor Supply",
};

type ScrapeStatus = {
  scrape_running: boolean;
  job_started_at: string | null;
  last_finished_at: string | null;
  last_saved_rows: number | null;
  last_error: string | null;
  last_scrape_debug: ScrapeDebug | null;
  active_target: string | null;
};

const empty: ScrapeStatus = {
  scrape_running: false,
  job_started_at: null,
  last_finished_at: null,
  last_saved_rows: null,
  last_error: null,
  last_scrape_debug: null,
  active_target: null,
};

function formatSince(iso: string | null) {
  if (!iso) return null;
  try {
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return null;
    const mins = Math.floor((Date.now() - t) / 60000);
    if (mins <= 0) return "just now";
    if (mins === 1) return "1 min";
    return `${mins} min`;
  } catch {
    return null;
  }
}

function ScrapeDebugPanel({ debug }: { debug: ScrapeDebug }) {
  const sites = debug.sites;
  if (!sites) return null;
  const rows = Object.entries(sites).sort(([a], [b]) => a.localeCompare(b));

  return (
    <details className="mt-2 text-xs border border-emerald-300/60 rounded-md bg-white/60 px-2 py-1.5 max-w-full">
      <summary className="cursor-pointer font-medium text-emerald-950 select-none">
        Per-site scrape debug
      </summary>
      <p className="text-emerald-900/75 mt-2 mb-1.5 leading-snug">
        <code className="text-[11px]">ok</code> = rows the scraper marked successful.{" "}
        <code className="text-[11px]">blocked</code> usually means bot protection or
        timeouts. <code className="text-[11px]">queued</code> = rows this run tried to
        insert (top {debug.top_n ?? "?"} ranked per site when not blocked).
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-[11px] text-emerald-950">
          <thead>
            <tr className="border-b border-emerald-200/80">
              <th className="py-1 pr-2 font-semibold">Site</th>
              <th className="py-1 pr-2 font-semibold">Returned</th>
              <th className="py-1 pr-2 font-semibold">OK rows</th>
              <th className="py-1 pr-2 font-semibold">Blocked</th>
              <th className="py-1 pr-2 font-semibold">Queued DB</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([site, d]) => (
              <tr key={site} className="border-b border-emerald-100/80 align-top">
                <td className="py-1 pr-2 font-mono">{site}</td>
                <td className="py-1 pr-2 tabular-nums">
                  {d.scraper_returned_rows ?? 0}
                </td>
                <td className="py-1 pr-2 tabular-nums">{d.ok_product_rows ?? 0}</td>
                <td className="py-1 pr-2">{d.blocked ? "yes" : "no"}</td>
                <td className="py-1 pr-2 tabular-nums">
                  {d.rows_queued_for_db ?? 0}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.map(([site, d]) =>
        d.scrape_notes ? (
          <p
            key={`${site}-note`}
            className="mt-1.5 font-mono text-[10px] text-emerald-900/80 break-all"
          >
            <span className="font-semibold text-emerald-950">{site}:</span>{" "}
            {d.scrape_notes}
          </p>
        ) : null
      )}
    </details>
  );
}

export default function ScrapeStatusBanner() {
  const [s, setS] = useState<ScrapeStatus>(empty);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch("/api/scrape-status", { cache: "no-store" });
        const data = (await res.json()) as Partial<ScrapeStatus>;
        if (!cancelled) {
          setS({
            scrape_running: Boolean(data.scrape_running),
            job_started_at: data.job_started_at ?? null,
            last_finished_at: data.last_finished_at ?? null,
            last_saved_rows:
              typeof data.last_saved_rows === "number"
                ? data.last_saved_rows
                : null,
            last_error: data.last_error ?? null,
            last_scrape_debug:
              data.last_scrape_debug &&
              typeof data.last_scrape_debug === "object" &&
              "sites" in data.last_scrape_debug
                ? (data.last_scrape_debug as ScrapeDebug)
                : null,
            active_target: data.active_target ?? null,
          });
        }
      } catch {
        if (!cancelled) setS(empty);
      }
    }

    tick();
    const id = setInterval(tick, 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const since = formatSince(s.job_started_at);

  if (s.scrape_running) {
    if (s.active_target && s.active_target !== "__all__") {
      return null;
    }
    return (
      <div className="border-b border-amber-200/80 bg-amber-50/90 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-2.5 flex items-start gap-3 text-sm text-amber-950">
          <span
            className="inline-flex h-2 w-2 shrink-0 rounded-full bg-amber-500 animate-pulse mt-1.5"
            aria-hidden
          />
          <p>
            <span className="font-semibold">Playwright is running</span>
            <span className="text-amber-900/80">
              {" "}
              — scraping all retailers. This can take many minutes on slow
              pages.
            </span>
            {since && (
              <span className="block text-xs text-amber-900/70 mt-1">
                Started ~{since} ago (wall clock).
              </span>
            )}
          </p>
        </div>
      </div>
    );
  }

  if (s.last_error) {
    return (
      <div className="border-b border-rose-200/90 bg-rose-50/95 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-2.5 text-sm text-rose-950">
          <p className="font-semibold">Last scrape failed</p>
          <p className="text-xs text-rose-900/85 mt-1 font-mono break-all">
            {s.last_error}
          </p>
          <p className="text-xs text-rose-800/80 mt-2">
            Check the API terminal for a full traceback. Fix the error, then run
            scrape again.
          </p>
        </div>
      </div>
    );
  }

  const finishedMs = s.last_finished_at
    ? Date.parse(s.last_finished_at)
    : NaN;
  const recentSuccess =
    !Number.isNaN(finishedMs) &&
    Date.now() - finishedMs < 45 * 60 * 1000 &&
    typeof s.last_saved_rows === "number";

  if (recentSuccess) {
    return (
      <div className="border-b border-emerald-200/80 bg-emerald-50/90 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-2 text-sm text-emerald-950">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-semibold">Last scrape finished</span>
            <span className="text-emerald-900/85">
              Saved {s.last_saved_rows} row{s.last_saved_rows === 1 ? "" : "s"} to
              the database. Refresh if cards look stale.
            </span>
          </div>
          <p className="text-xs text-emerald-800/90 mt-1.5 leading-relaxed">
            Only retailers that returned <strong>ok</strong> listings and passed
            ranking appear as product cards. Others are often blocked (CAPTCHA,
            layout change, timeout). Open{" "}
            <strong>Per-site scrape debug</strong> below for this run.
          </p>
          {s.last_scrape_debug ? (
            <ScrapeDebugPanel debug={s.last_scrape_debug} />
          ) : null}
        </div>
      </div>
    );
  }

  return null;
}
