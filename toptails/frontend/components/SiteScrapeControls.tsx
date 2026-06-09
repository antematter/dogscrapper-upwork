"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

export type SiteDebugInfo = {
  top_n?: number;
  scraper_returned_rows?: number;
  ok_product_rows?: number;
  blocked?: boolean;
  silent_empty?: boolean;
  scrape_notes?: string | null;
  rows_queued_for_db?: number;
};

type SiteScrapeStatus = {
  site: string;
  scrape_running: boolean;
  global_scrape_running: boolean;
  active_target: string | null;
  job_started_at: string | null;
  last_finished_at: string | null;
  last_saved_rows: number | null;
  last_error: string | null;
  debug: SiteDebugInfo | null;
};

function formatSince(iso: string | null) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins <= 0) return "just now";
  if (mins === 1) return "1 min";
  return `${mins} min`;
}

function SiteDebugInline({ debug, site }: { debug: SiteDebugInfo; site: string }) {
  return (
    <div className="mt-3 w-full rounded-lg border border-stone-200 bg-stone-50/90 px-3 py-2.5 text-xs text-stone-800">
      <p className="font-semibold text-stone-900 mb-1.5">Last scrape for this site</p>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1 tabular-nums">
        <div>
          <dt className="text-stone-500">Returned</dt>
          <dd>{debug.scraper_returned_rows ?? 0}</dd>
        </div>
        <div>
          <dt className="text-stone-500">OK rows</dt>
          <dd>{debug.ok_product_rows ?? 0}</dd>
        </div>
        <div>
          <dt className="text-stone-500">Blocked</dt>
          <dd>{debug.blocked ? "yes" : "no"}</dd>
        </div>
        <div>
          <dt className="text-stone-500">Queued DB</dt>
          <dd>{debug.rows_queued_for_db ?? 0}</dd>
        </div>
      </dl>
      {debug.scrape_notes ? (
        <p className="mt-2 font-mono text-[10px] text-stone-600 break-all leading-relaxed">
          {debug.scrape_notes}
        </p>
      ) : (debug.ok_product_rows ?? 0) === 0 ? (
        <p className="mt-2 text-[10px] text-amber-900 leading-relaxed">
          No products parsed. Check backend logs and{" "}
          <span className="font-mono">debug_scrapes/{site}/</span> screenshots.
        </p>
      ) : null}
    </div>
  );
}

export default function SiteScrapeControls({
  site,
  siteLabel,
}: {
  site: string;
  siteLabel: string;
}) {
  const router = useRouter();
  const [status, setStatus] = useState<SiteScrapeStatus | null>(null);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const wasRunning = useRef(false);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`/api/scrape-status/${site}`, { cache: "no-store" });
      const data = (await res.json()) as SiteScrapeStatus;
      setStatus(data);
      return data;
    } catch {
      return null;
    }
  }, [site]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 2500);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    if (!status) return;
    if (wasRunning.current && !status.scrape_running) {
      router.refresh();
    }
    wasRunning.current = status.scrape_running;
  }, [status?.scrape_running, status, router]);

  const busyElsewhere =
    Boolean(status?.global_scrape_running) &&
    status?.active_target !== site &&
    !status?.scrape_running;

  const running = Boolean(status?.scrape_running);
  const disabled = submitting || running || busyElsewhere;

  async function handleScrape() {
    setSubmitting(true);
    setTriggerError(null);
    try {
      const res = await fetch(`/api/trigger/${site}`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setTriggerError(
          typeof body.error === "string" ? body.error : `Request failed (${res.status})`
        );
        return;
      }
      await poll();
    } catch (e) {
      setTriggerError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  const since = formatSince(status?.job_started_at ?? null);
  const recentFinish =
    status?.last_finished_at &&
    Date.now() - Date.parse(status.last_finished_at) < 45 * 60 * 1000;

  const showDebug =
    Boolean(status?.debug) && !running && Boolean(recentFinish);

  return (
    <div className="w-full sm:w-auto flex flex-col items-stretch sm:items-end gap-1">
      <button
        type="button"
        onClick={handleScrape}
        disabled={disabled}
        className="text-xs font-semibold px-2.5 py-1.5 rounded-md border border-stone-300 bg-white text-stone-800 hover:bg-stone-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors self-end"
        title={
          busyElsewhere
            ? "Another scrape is already running"
            : `Run Playwright scraper for ${siteLabel} only`
        }
      >
        {running ? "Scraping…" : submitting ? "Starting…" : "Scrape site"}
      </button>
      {running && since ? (
        <span className="text-[10px] text-amber-800 tabular-nums self-end">
          ~{since}
        </span>
      ) : null}
      {busyElsewhere ? (
        <span className="text-[10px] text-stone-500 self-end text-right leading-tight">
          Busy (
          {status?.active_target === "__all__" ? "all sites" : status?.active_target})
        </span>
      ) : null}
      {triggerError ? (
        <p className="text-[10px] text-rose-700 text-right break-all">{triggerError}</p>
      ) : null}
      {status?.last_error && !running ? (
        <p className="text-[10px] text-rose-800 text-right font-mono break-all">
          {status.last_error}
        </p>
      ) : null}
      {status?.last_saved_rows != null &&
      !status.last_error &&
      !running &&
      recentFinish ? (
        <span className="text-[10px] text-emerald-800 self-end">
          Saved {status.last_saved_rows} row{status.last_saved_rows === 1 ? "" : "s"}
        </span>
      ) : null}
      {showDebug && status?.debug ? (
        <SiteDebugInline debug={status.debug} site={site} />
      ) : null}
    </div>
  );
}
