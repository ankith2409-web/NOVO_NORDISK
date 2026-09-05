/**
 * Which theme the page is currently in.
 *
 * Read from the document rather than passed down, because the one thing that
 * needs it is a Google map five levels below the switch that sets it, and
 * threading a prop through five components to reach one leaf is how a codebase
 * acquires props that exist only to be forwarded.
 *
 * The root element is the single source of truth here anyway: `App` writes
 * `data-theme` onto it, every stylesheet rule keys off it, and an observer on
 * that attribute is therefore watching the same thing the CSS is. A theme
 * change after the map is built has to rebuild it -- Google decides its own
 * colours at construction -- so this has to be reactive rather than read once.
 */
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

function current(): Theme {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === "light" || stamped === "dark") return stamped;
  // Nothing stamped means "follow the system", which is the default state.
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function useCurrentTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(current);

  useEffect(() => {
    const watch = new MutationObserver(() => setTheme(current()));
    watch.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    // And the system setting, for a page that has not been stamped at all.
    const system = window.matchMedia?.("(prefers-color-scheme: dark)");
    const follow = () => setTheme(current());
    system?.addEventListener("change", follow);
    return () => {
      watch.disconnect();
      system?.removeEventListener("change", follow);
    };
  }, []);

  return theme;
}
