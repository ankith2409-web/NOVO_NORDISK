/**
 * The address bar as the source of truth for where you are.
 *
 * This interface remembered its state in `localStorage` and nowhere else,
 * which worked perfectly for one person on one machine and failed at the thing
 * the tool is actually for. Its whole purpose is review: somebody opens a
 * measure, sees a figure they want a second opinion on, and sends it to a
 * colleague. There was no link to send. `#dashboard` did nothing, the back
 * button did nothing, and a reload landed wherever that browser happened to
 * have been last -- which for a reviewer following a reference is the wrong
 * page under the right name.
 *
 * Hash rather than path, because the interface is served as one prebuilt file
 * with no server-side routing: `/dashboard` would have to be rewritten to the
 * app by every deployment, and the one that forgot would serve a 404 to
 * exactly the person who was sent a link.
 *
 * The model is always named, never implied. A link that resolves to whichever
 * model a server happens to default to is a link that means different things
 * in different places, and this project's whole argument is that a figure
 * without its context is not a figure.
 */

export interface Route {
  view: string;
  model: string;
}

/** `#/dashboard?model=Store%20Sales` -> `{view, model}`. */
export function readRoute(hash: string = window.location.hash): Partial<Route> {
  const raw = hash.replace(/^#\/?/, "");
  if (!raw) return {};
  const [path, query = ""] = raw.split("?");
  const found: Partial<Route> = {};
  const view = decodeURIComponent(path).trim();
  if (view) found.view = view;
  // `URLSearchParams` decodes `+` as a space, which is wrong for a model
  // called `A+B`; the values here are written with `encodeURIComponent`, so
  // they are read back the same way.
  for (const pair of query.split("&")) {
    const [key, value = ""] = pair.split("=");
    if (key === "model" && value) found.model = decodeURIComponent(value);
  }
  return found;
}

/** The address for a place in the app, as a string anyone can paste. */
export function routeToHash(route: Route): string {
  const model = route.model ? `?model=${encodeURIComponent(route.model)}` : "";
  return `#/${encodeURIComponent(route.view)}${model}`;
}

/**
 * Put the address bar where the app is, without inventing history entries.
 *
 * `push` is for somewhere a reader chose to go, so Back returns them to where
 * they were. Everything else -- restoring a remembered view on load, or naming
 * the model once it resolves -- replaces, because a Back button that walks
 * through a page's own start-up is a Back button nobody trusts.
 */
export function writeRoute(route: Route, how: "push" | "replace" = "push"): void {
  const next = routeToHash(route);
  if (next === window.location.hash) return;
  try {
    const url = window.location.pathname + window.location.search + next;
    if (how === "push") window.history.pushState(null, "", url);
    else window.history.replaceState(null, "", url);
  } catch {
    // Some embeddings disallow history writes. A working app with a stale
    // address bar beats a blank page.
    window.location.hash = next;
  }
}
