"use client";

import { useCallback, useState } from "react";
import ProductCard from "@/components/ProductCard";
import SiteScrapeControls from "@/components/SiteScrapeControls";

interface Product {
  source_site?: string | null;
  title: string;
  price: number | null;
  avg_rating: number | null;
  review_count: number | null;
  trust_score: number | null;
  product_url: string | null;
  image_url: string | null;
}

type ChewyProductsResponse = {
  site: string;
  scrape_status: string;
  scrape_notes: string | null;
  top_products: Product[];
};

export default function ChewySiteSection() {
  const [products, setProducts] = useState<Product[]>([]);
  const [scrapeStatus, setScrapeStatus] = useState<string | null>(null);
  const [scrapeNotes, setScrapeNotes] = useState<string | null>(null);
  const [loadingProducts, setLoadingProducts] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadedAfterScrape, setLoadedAfterScrape] = useState(false);

  const loadProducts = useCallback(async () => {
    setLoadingProducts(true);
    setLoadError(null);
    try {
      const res = await fetch("/api/products/chewy", { cache: "no-store" });
      const body = (await res.json()) as ChewyProductsResponse & { error?: string };
      if (!res.ok) {
        throw new Error(body.error ?? `Request failed (${res.status})`);
      }
      setScrapeStatus(body.scrape_status);
      setScrapeNotes(body.scrape_notes);
      setProducts(body.top_products ?? []);
      setLoadedAfterScrape(true);
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setLoadingProducts(false);
    }
  }, []);

  const isBlocked = loadedAfterScrape && scrapeStatus !== "ok";
  const hasProducts = products.length > 0;

  return (
    <section className="rounded-xl border border-stone-200/90 bg-white shadow-sm shadow-stone-950/5 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-stone-100 bg-stone-50/80">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className="h-8 w-1 shrink-0 rounded-full bg-sky-600"
            aria-hidden
          />
          <span className="font-semibold text-stone-900 truncate">Chewy</span>
        </div>
        {isBlocked ? (
          <span className="text-xs font-medium text-stone-500 tabular-nums px-2 py-1 rounded-md bg-stone-100 border border-stone-200/80">
            Unavailable
          </span>
        ) : hasProducts ? (
          <span className="text-xs font-medium text-emerald-800 tabular-nums px-2 py-1 rounded-md bg-emerald-50 border border-emerald-200/70">
            In catalog
          </span>
        ) : (
          <span className="text-xs font-medium text-amber-900 tabular-nums px-2 py-1 rounded-md bg-amber-50 border border-amber-200/70">
            No rows yet
          </span>
        )}
      </div>

      <div className="p-4 sm:p-5">
        <SiteScrapeControls
          site="chewy"
          siteLabel="Chewy"
          refreshOnComplete={false}
          onScrapeComplete={loadProducts}
        />

        {loadingProducts ? (
          <p className="text-sm text-stone-500 py-2">Loading Chewy products…</p>
        ) : loadError ? (
          <p className="text-sm text-rose-700 py-2">{loadError}</p>
        ) : isBlocked ? (
          <div className="rounded-lg border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-700">
            <p className="font-medium text-stone-800">Could not scrape this site</p>
            <p className="mt-1 text-stone-600 text-sm leading-relaxed">
              {scrapeNotes ??
                "Often blocked by bot protection without retailer API keys."}
            </p>
          </div>
        ) : !hasProducts ? (
          <p className="text-sm text-stone-500 py-2">
            Chewy products are not loaded automatically. Click{" "}
            <strong>Scrape site</strong> above to run the scraper and load
            results here.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {products.map((p, i) => (
              <ProductCard
                key={`chewy-${i}-${p.product_url ?? p.title}`}
                {...p}
                source_site="chewy"
                rank={i + 1}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
