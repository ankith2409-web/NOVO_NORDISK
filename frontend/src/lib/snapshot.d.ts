/**
 * `snapshot.json` is generated locally by `scripts/capture_snapshot.py` and
 * gitignored -- it is captured demo data, not source, so a fresh clone never
 * has it until someone runs the capture step.
 *
 * `tsc -b` resolves every import it can see regardless of whether the code
 * path that reaches it ever runs, so without this the normal build fails on a
 * clean checkout even though nothing at runtime touches this file unless
 * `SNAPSHOT_MODE` is on. An exact-path declaration does not work here --
 * TypeScript resolves a relative specifier against the filesystem before
 * consulting ambient declarations, so it still fails when the file is
 * missing. Only the wildcard form, the same mechanism `vite/client` uses for
 * `*.svg` and other asset imports, is honoured ahead of filesystem
 * resolution.
 */
declare module "*.json" {
  const data: Record<string, unknown>;
  export default data;
}
