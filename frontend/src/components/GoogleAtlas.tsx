/**
 * The same measure, on Google's map.
 *
 * The built-in map draws its own tiles and does its own Web Mercator, which is
 * right when nothing else is available and is not what somebody means when
 * they say "put it on Google Maps". This is the other one: Google's tiles,
 * Google's projection, Google's pan and zoom, with the model's figures drawn
 * on top of it.
 *
 * Three decisions worth stating.
 *
 * **Google places the points, not this code.** Each store is a
 * `google.maps.Circle` given a centre in degrees and a radius in metres, so
 * the projection that positions it is the same one that positions the streets
 * under it. The built-in map has to do that arithmetic itself and is tested
 * for it; here, doing it again would be a second implementation to keep in
 * agreement with the first for no gain.
 *
 * **The script is loaded once, on demand, and only when a key exists.** A
 * server with no key never reaches this component at all, so no request is
 * made and nothing about the page changes -- which matters because running
 * offline is a thing this tool is expected to do.
 *
 * **A failure says so.** A key that is missing, restricted to another domain,
 * or over quota fails inside Google's script rather than in a fetch this code
 * can inspect, so the load is given a deadline and the panel falls back to the
 * built-in map when it passes. A blank grey rectangle with no explanation is
 * the one outcome worth engineering against.
 */
import { useEffect, useRef, useState } from "react";
import { cx } from "@/lib/cx";

/** How long to wait for Google's script before giving up on it. */
const PATIENCE = 8000;

//: Radius of the largest bubble, as a fraction of the span the points cover.
//: Proportional rather than fixed, because these are metres on the ground: a
//: radius that suits thirteen stores across a city would be invisible across a
//: continent and would swallow a business park.
const BIGGEST = 0.06;

declare global {
  interface Window {
    google?: any;
    __concordanceMapsReady?: Promise<void>;
  }
}

/**
 * The Maps script, fetched at most once per page.
 *
 * Held on `window` rather than in a module variable because the promise has to
 * outlive this component: a reader who switches to another view and back must
 * not trigger a second `<script>` for an API that is already loaded, and
 * loading the Maps API twice logs a warning and can leave two copies of it
 * fighting over the same global.
 */
function load(key: string): Promise<void> {
  if (window.google?.maps) return Promise.resolve();
  if (window.__concordanceMapsReady) return window.__concordanceMapsReady;

  window.__concordanceMapsReady = new Promise<void>((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly`;
    tag.async = true;
    tag.onload = () => resolve();
    tag.onerror = () => reject(new Error("the Google Maps script could not be loaded"));
    document.head.appendChild(tag);
    // Google reports a bad key by drawing an error into the container rather
    // than by failing the script, so `onerror` alone would wait forever.
    setTimeout(
      () => reject(new Error("the Google Maps script did not load in time")),
      PATIENCE,
    );
  });
  return window.__concordanceMapsReady;
}

export function GoogleAtlas({
  places,
  measure,
  label,
  apiKey,
  theme,
  onFallback,
}: {
  places: { label: string; lat: number; lon: number; value: number }[];
  measure: string;
  /** What one point is, e.g. "Store". */
  label: string;
  apiKey: string;
  theme: "light" | "dark";
  /** Called when Google cannot be used, so the panel can draw the built-in map. */
  onFallback: (reason: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;

    load(apiKey)
      .then(() => {
        if (!live || !host.current || !window.google?.maps) return;
        const maps = window.google.maps;

        const bounds = new maps.LatLngBounds();
        for (const place of places) bounds.extend({ lat: place.lat, lng: place.lon });

        const map = new maps.Map(host.current, {
          mapTypeControl: true,
          streetViewControl: false,
          fullscreenControl: true,
          // Google's own dark styling, so the map follows the app's theme
          // rather than glowing white inside a dark page.
          ...(theme === "dark" ? { colorScheme: "DARK" } : {}),
        });
        map.fitBounds(bounds, 48);

        // Metres, from the span the points actually cover -- see BIGGEST.
        const span =
          maps.geometry?.spherical?.computeDistanceBetween?.(
            bounds.getNorthEast(),
            bounds.getSouthWest(),
          ) ?? 40000;
        const biggest = Math.max(...places.map((p) => Math.abs(p.value)), 1);

        const note = new maps.InfoWindow();
        for (const place of places) {
          // Area proportional to the value, as everywhere else in this
          // interface: a circle of twice the radius reads as four times the
          // quantity, which would overstate every large point.
          const share = Math.sqrt(Math.abs(place.value) / biggest);
          const circle = new maps.Circle({
            map,
            center: { lat: place.lat, lng: place.lon },
            radius: Math.max(span * BIGGEST * share, span * 0.008),
            strokeColor: "#0f6e72",
            strokeOpacity: 0.9,
            strokeWeight: 1.4,
            fillColor: "#0f6e72",
            fillOpacity: 0.42,
            clickable: true,
          });
          circle.addListener("click", () => {
            setActive(place.label);
            note.setContent(
              `<div style="font:13px system-ui;padding:2px 4px">` +
                `<strong>${escapeHtml(place.label)}</strong><br>` +
                `${escapeHtml(measure)}: ${place.value.toLocaleString()}` +
                `</div>`,
            );
            note.setPosition({ lat: place.lat, lng: place.lon });
            note.open(map);
          });
        }
        setReady(true);
      })
      .catch((failure: Error) => {
        if (live) onFallback(failure.message);
      });

    return () => {
      live = false;
    };
    // Rebuilt when the data or the theme changes; `onFallback` is stable at
    // the call site and is deliberately not a dependency, or every render
    // would tear the map down and build it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, theme, places, measure]);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={host}
        role="img"
        aria-label={`${measure} by ${label}, on Google Maps`}
        className={cx(
          "h-[340px] w-full rounded border border-hairline bg-surface",
          !ready && "animate-pulse",
        )}
      />
      <p className="min-h-[1.4em] text-[11.5px] text-muted tabular" aria-live="polite">
        {active
          ? `${active} — ${
              places.find((p) => p.label === active)?.value.toLocaleString() ?? ""
            }`
          : `${places.length} ${label.toLowerCase()}s, sized by ${measure}. Click one for its figure.`}
      </p>
    </div>
  );
}

/** A label is data out of the model, and this puts it into an HTML string. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
