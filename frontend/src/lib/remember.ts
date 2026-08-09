/**
 * The small amount of interface state that should survive a refresh.
 *
 * Which view someone is on, which model they picked, whether the copilot is
 * open: reloading and being dropped back on the Overview of the first model is
 * a small thing that happens constantly, and it is felt most during a demo,
 * where the reload is usually deliberate.
 *
 * Nothing here is data. It is where the user was, and it lives in localStorage
 * because that is the honest scope for it -- one person, one browser, no
 * server, no account, and nothing worth reading if someone else opens the same
 * browser. Anything that belongs to the *model* rather than the viewer is
 * derived from the model on every load and is deliberately not stored, so a
 * stale cache can never be shown as fact.
 *
 * Every read is validated by the caller against what this server actually
 * holds. A remembered view whose flag is now absent, or a remembered model this
 * server never loaded, has to fall back rather than be trusted -- otherwise a
 * value from one server's session silently governs the next one.
 */

const PREFIX = "concordance:";

export function remember(key: string, value: string): void {
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch {
    // Private-browsing modes and a full quota both throw here. Losing the
    // memory of which tab was open is not worth failing a render over.
  }
}

export function recall(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

/**
 * Recall a value only if it is still one of the choices available now.
 *
 * The validation is the point rather than a nicety: the same browser is used
 * against different servers holding different models with different flags, and
 * a remembered choice that no longer exists would otherwise send every request
 * to something that is not there.
 */
export function recallOneOf<T extends string>(key: string, allowed: readonly T[]): T | null {
  const stored = recall(key);
  return stored !== null && (allowed as readonly string[]).includes(stored)
    ? (stored as T)
    : null;
}
