"use client";

import { useState } from "react";

interface ProductCardProps {
  source_site?: string | null;
  title: string;
  price: number | null;
  avg_rating: number | null;
  review_count: number | null;
  trust_score: number | null;
  product_url: string | null;
  image_url: string | null;
  rank: number;
}

const SITE_LABELS: Record<string, string> = {
  chewy: "Chewy",
  amazon: "Amazon",
  walmart: "Walmart",
  petsmart: "PetSmart",
  petco: "Petco",
  target: "Target",
  tractor_supply: "Tractor Supply",
};

function hasStarRating(
  avg_rating: number | null,
  review_count: number | null
): boolean {
  return (
    avg_rating != null &&
    avg_rating > 0 &&
    (review_count == null || review_count > 0)
  );
}

function trustScoreLabel(
  trust_score: number | null,
  review_count: number | null
): string {
  if (trust_score == null) return "Trust score not computed";
  if (trust_score <= 0) {
    if (review_count != null && review_count < 15) {
      return "Trust score needs at least 15 reviews";
    }
    if (review_count == null || review_count === 0) {
      return "No review data from retailer yet";
    }
    return "Trust score unavailable for this listing";
  }
  return `Trust score ${Math.round(trust_score * 100)}% (reviews, rating, quality signals)`;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3 text-xs">
      <dt className="text-stone-500 font-medium shrink-0 sm:w-28">{label}</dt>
      <dd className="text-stone-800 break-all">{value}</dd>
    </div>
  );
}

function Stars({ rating }: { rating: number }) {
  return (
    <div className="flex items-center gap-0.5" aria-hidden>
      {[1, 2, 3, 4, 5].map((i) => {
        const filled = rating >= i;
        const half = !filled && rating >= i - 0.5;
        return (
          <svg key={i} className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="none">
            {filled ? (
              <path
                d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
                fill="#d97706"
              />
            ) : half ? (
              <>
                <defs>
                  <linearGradient id={`half-${i}`} x1="0" x2="1" y1="0" y2="0">
                    <stop offset="50%" stopColor="#d97706" />
                    <stop offset="50%" stopColor="#e7e5e4" />
                  </linearGradient>
                </defs>
                <path
                  d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
                  fill={`url(#half-${i})`}
                />
              </>
            ) : (
              <path
                d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
                fill="#e7e5e4"
              />
            )}
          </svg>
        );
      })}
    </div>
  );
}

function TrustBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const cls =
    pct >= 75 ? "trust-high" : pct >= 50 ? "trust-mid" : "trust-low";
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-md ${cls}`}
    >
      <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
        <path
          fillRule="evenodd"
          d="M10 1l2.39 4.843L18 6.854l-4 3.9.944 5.505L10 13.772l-4.944 2.487L6 10.754 2 6.854l5.61-1.011L10 1z"
          clipRule="evenodd"
        />
      </svg>
      Trust {pct}%
    </span>
  );
}

export default function ProductCard({
  source_site,
  title,
  price,
  avg_rating,
  review_count,
  trust_score,
  product_url,
  image_url,
  rank,
}: ProductCardProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const retailer =
    (source_site && SITE_LABELS[source_site]) || source_site || "Retailer";
  const showStars = hasStarRating(avg_rating, review_count);
  const showTrustBadge = trust_score != null && trust_score > 0;

  return (
    <article className="relative flex flex-col rounded-xl border border-stone-200 bg-white overflow-hidden hover:border-stone-300 hover:shadow-md hover:shadow-stone-950/5 transition-[box-shadow,border-color]">
      {rank === 1 && (
        <div className="absolute top-2.5 left-2.5 z-10 text-[10px] font-bold uppercase tracking-wide text-amber-950 bg-amber-300/95 px-2 py-0.5 rounded">
          Top pick
        </div>
      )}

      <div className="relative h-40 sm:h-44 bg-stone-100 flex items-center justify-center">
        {image_url && !imgFailed ? (
          <img
            src={image_url}
            alt={title}
            className="max-h-full max-w-full object-contain p-3"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <span className="text-stone-400 text-xs font-medium uppercase tracking-wider">
            No image
          </span>
        )}
      </div>

      <div className="p-4 flex flex-col gap-2 flex-1 border-t border-stone-100">
        <h2 className="text-sm font-semibold text-stone-900 line-clamp-2 leading-snug min-h-[2.5rem]">
          {title}
        </h2>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-lg font-bold text-stone-900 tabular-nums">
            {price != null ? (
              `$${price.toFixed(2)}`
            ) : (
              <span className="text-stone-400 text-sm font-medium">
                Price n/a
              </span>
            )}
          </span>
          {showTrustBadge && <TrustBadge score={trust_score} />}
        </div>

        {showStars ? (
          <div className="flex items-center gap-2 flex-wrap">
            <Stars rating={avg_rating!} />
            <span className="text-xs text-stone-500 tabular-nums">
              {avg_rating!.toFixed(1)}
              {review_count != null &&
                review_count > 0 &&
                ` · ${review_count.toLocaleString()} reviews`}
            </span>
          </div>
        ) : (
          <p className="text-xs text-stone-500">No star rating on listing page</p>
        )}

        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          className="flex items-center justify-between w-full text-left text-xs font-semibold text-stone-600 hover:text-stone-900 py-1.5 px-2 -mx-2 rounded-lg hover:bg-stone-50 transition-colors"
        >
          <span>{expanded ? "Hide details" : "Show details"}</span>
          <svg
            className={`w-4 h-4 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {expanded && (
          <dl className="rounded-lg border border-stone-100 bg-stone-50/80 p-3 space-y-2.5">
            <DetailRow label="Retailer" value={retailer} />
            <DetailRow
              label="Price"
              value={
                price != null
                  ? `$${price.toFixed(2)}`
                  : "Not on listing page (many sites load price via JavaScript)"
              }
            />
            <DetailRow
              label="Rating"
              value={
                showStars
                  ? `${avg_rating!.toFixed(1)} stars${
                      review_count != null && review_count > 0
                        ? ` · ${review_count.toLocaleString()} reviews`
                        : ""
                    }`
                  : "Not available from scrape"
              }
            />
            <DetailRow label="Trust" value={trustScoreLabel(trust_score, review_count)} />
            <DetailRow
              label="Image"
              value={image_url && !imgFailed ? "Loaded" : "Missing or failed to load"}
            />
            {product_url && (
              <DetailRow label="Link" value={product_url.replace(/^https?:\/\//, "")} />
            )}
          </dl>
        )}

        {product_url && (
          <a
            href={product_url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`View ${title} on retailer site`}
            className="mt-auto pt-1 block w-full text-center text-sm font-semibold bg-stone-900 text-white rounded-lg py-2 hover:bg-stone-800 transition-colors"
          >
            View product
          </a>
        )}
      </div>
    </article>
  );
}
