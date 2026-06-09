import ProductCard from "@/components/ProductCard";
import ScrapeStatusBanner from "@/components/ScrapeStatusBanner";
import SiteScrapeControls from "@/components/SiteScrapeControls";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";

const SITE_STYLE: Record<string, { label: string; swatch: string }> = {
  chewy: { label: "Chewy", swatch: "bg-sky-600" },
  amazon: { label: "Amazon", swatch: "bg-amber-600" },
  walmart: { label: "Walmart", swatch: "bg-blue-700" },
  petsmart: { label: "PetSmart", swatch: "bg-emerald-600" },
  petco: { label: "Petco", swatch: "bg-cyan-600" },
  target: { label: "Target", swatch: "bg-red-600" },
  tractor_supply: { label: "Tractor Supply", swatch: "bg-lime-700" },
};

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

interface SiteResult {
  site: string;
  scrape_status: string;
  scrape_notes: string | null;
  top_products: Product[];
}

interface ProductsResponse {
  category: string;
  generated_at: string;
  results: SiteResult[];
}

async function fetchProducts(): Promise<ProductsResponse | null> {
  try {
    const res = await fetch(`${API_URL}/products?category=dog_beds&top_n=2`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function SiteSection({ result }: { result: SiteResult }) {
  const meta = SITE_STYLE[result.site] ?? {
    label: result.site,
    swatch: "bg-stone-500",
  };
  const isBlocked = result.scrape_status !== "ok";
  const hasProducts = result.top_products.length > 0;

  return (
    <section className="rounded-xl border border-stone-200/90 bg-white shadow-sm shadow-stone-950/5 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-stone-100 bg-stone-50/80">
        <div className="flex items-center gap-3 min-w-0">
          <span
            className={`h-8 w-1 shrink-0 rounded-full ${meta.swatch}`}
            aria-hidden
          />
          <span className="font-semibold text-stone-900 truncate">
            {meta.label}
          </span>
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
        <SiteScrapeControls site={result.site} siteLabel={meta.label} />
        {isBlocked ? (
          <div className="rounded-lg border border-stone-200 bg-stone-50 px-4 py-3 text-sm text-stone-700">
            <p className="font-medium text-stone-800">Could not scrape this site</p>
            <p className="mt-1 text-stone-600 text-sm leading-relaxed">
              {result.scrape_notes ??
                "Often blocked by bot protection without retailer API keys."}
            </p>
          </div>
        ) : !hasProducts ? (
          <p className="text-sm text-stone-500 py-2">
            Nothing stored for this site yet. Use <strong>Scrape site</strong> above
            when the backend is up.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {result.top_products.map((p, i) => (
              <ProductCard
                key={`${result.site}-${i}-${p.product_url ?? p.title}`}
                {...p}
                rank={i + 1}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default async function Home() {
  const data = await fetchProducts();

  const hasAnyProducts = data?.results.some((r) => r.top_products.length > 0);

  return (
    <div className="min-h-screen flex flex-col bg-stone-50 text-stone-900">
      <header className="sticky top-0 z-20 border-b border-stone-200/90 bg-white/90 backdrop-blur-md">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-base font-bold tracking-tight text-stone-900 truncate">
              TopTails
            </span>
            <span className="hidden sm:inline text-[11px] uppercase tracking-wider font-semibold text-stone-500 bg-stone-100 border border-stone-200/80 px-2 py-0.5 rounded">
              Dog beds
            </span>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {data?.generated_at && (
              <span className="hidden md:inline text-xs text-stone-500 tabular-nums">
                <time suppressHydrationWarning dateTime={data.generated_at}>
                  {new Date(data.generated_at).toLocaleString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </time>
              </span>
            )}
            <a
              href="/api/trigger"
              className="text-xs font-semibold bg-stone-900 text-white px-3 py-2 rounded-lg hover:bg-stone-800 transition-colors"
            >
              Run scrape
            </a>
          </div>
        </div>
      </header>

      <ScrapeStatusBanner />

      <main className="flex-1 max-w-5xl w-full mx-auto px-4 sm:px-6 py-8 sm:py-10">
        <div className="mb-8 sm:mb-10 max-w-2xl">
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-stone-900">
            Top dog beds by retailer
          </h1>
          <p className="mt-2 text-stone-600 text-sm sm:text-base leading-relaxed">
            Two picks per store, ordered by trust score (reviews, volume, and
            quality signals).
          </p>
        </div>

        {!data && (
          <div
            className="rounded-xl border border-red-200 bg-red-50 px-4 py-4 text-sm"
            role="alert"
          >
            <p className="font-semibold text-red-900">Backend unreachable</p>
            <p className="mt-1 text-red-800/90">
              Start the API at{" "}
              <code className="text-xs font-mono bg-red-100/80 px-1.5 py-0.5 rounded">
                {API_URL}
              </code>{" "}
              and reload.
            </p>
          </div>
        )}

        {data && !hasAnyProducts && (
          <div className="mb-8 rounded-xl border border-sky-200 bg-sky-50 px-4 py-4 text-sm text-sky-950">
            <p className="font-semibold">No products in the database yet</p>
            <p className="mt-1 text-sky-900/85 leading-relaxed">
              Use <strong>Run scrape</strong> above (or POST{" "}
              <code className="text-xs font-mono bg-white/70 px-1 rounded">
                /scrape/run
              </code>
              ). Playwright may run for several minutes.
            </p>
          </div>
        )}

        {data && (
          <div className="flex flex-col gap-5">
            {data.results.map((result) => (
              <SiteSection key={result.site} result={result} />
            ))}
          </div>
        )}
      </main>

      <footer className="border-t border-stone-200/90 py-6 mt-auto">
        <p className="text-center text-xs text-stone-500 px-4">
          TopTails MVP — trust scores are computed, not sponsored listings.
        </p>
      </footer>
    </div>
  );
}
