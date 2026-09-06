/**
 * A link to exactly what you are looking at, on the clipboard.
 *
 * Routing gave every page and every object an address. That is only half of
 * it: an address nobody can get hold of is not a link. The address bar does
 * carry it, but asking a reviewer to select a URL out of the chrome and trust
 * that it names the object rather than the page is the kind of instruction
 * that ends with them pasting the wrong thing.
 *
 * So the button says what it will copy -- "link to Net Sales" rather than
 * "share" -- because the whole point is that the link is *specific*, and a
 * generic label would hide the one fact worth knowing about it.
 */

import { useEffect, useState } from "react";

import { absoluteHref, type Route } from "@/lib/route";
import { cx } from "@/lib/cx";

export function CopyLink({
  route,
  what,
  className,
}: {
  route: Route;
  /** What the link points at, for the label and the announcement. */
  what: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!copied && !failed) return;
    const timer = window.setTimeout(() => {
      setCopied(false);
      setFailed(false);
    }, 2400);
    return () => window.clearTimeout(timer);
  }, [copied, failed]);

  async function copy() {
    const href = absoluteHref(route);
    try {
      // Only available over HTTPS and on localhost. A server reached by IP on
      // a colleague's machine is neither, which is exactly where somebody is
      // most likely to be sharing a link -- hence the fallback rather than a
      // button that silently does nothing.
      await navigator.clipboard.writeText(href);
      setCopied(true);
    } catch {
      if (!selectFallback(href)) setFailed(true);
      else setCopied(true);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={cx(
        "shrink-0 rounded px-1.5 py-0.5 text-[11.5px] whitespace-nowrap",
        "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        copied ? "text-accent" : failed ? "text-review" : "text-faint hover:text-accent",
        className,
      )}
      // Announced rather than only coloured: the change is the entire feedback,
      // and a colour shift is not feedback to a screen reader.
      aria-live="polite"
      title={failed ? "Could not reach the clipboard" : `Copy a link to ${what}`}
    >
      {copied ? "link copied" : failed ? "copy failed" : `link to ${what}`}
    </button>
  );
}

/**
 * The clipboard without the Clipboard API.
 *
 * `execCommand` is deprecated and is the only thing that works on a page
 * served over plain HTTP, which is how this tool is usually reached.
 */
function selectFallback(text: string): boolean {
  try {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(field);
    return ok;
  } catch {
    return false;
  }
}
