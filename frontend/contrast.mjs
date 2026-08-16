/**
 * WCAG contrast over the design tokens.
 *
 * The palette in `index.css` carries comments citing exact ratios -- 2.93:1,
 * 4.51:1 -- which means it was measured once and then maintained by hand. That
 * is fine until somebody shifts a colour and re-derives nothing, which is
 * exactly how `--color-faint` shipped at 2.93:1 the first time.
 *
 * So the measurement is a script rather than a memory. It reads the tokens out
 * of the stylesheet itself -- not a copy kept here, which would drift -- and
 * checks every pairing the interface actually renders, in both themes.
 *
 *   node contrast.mjs          # report, exits non-zero on any failure
 *
 * The 4.5:1 floor is WCAG 2.2 AA for text under 18.66px bold / 24px regular.
 * Every text token here is used below that size somewhere, so none of them get
 * the 3:1 large-text allowance.
 */
import { readFileSync } from 'fs';

const CSS = readFileSync(new URL('./src/index.css', import.meta.url), 'utf8');

/** Pull `--color-x: #hex;` pairs out of a block of CSS. */
function tokensIn(source) {
  const out = {};
  for (const [, name, hex] of source.matchAll(/--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    out[name] = hex;
  }
  return out;
}

// The dark theme redefines only some tokens, so it inherits the rest from the
// light block -- the same way the cascade does at runtime. Reading them as two
// independent sets would test a palette that never exists in a browser.
const darkBlock = CSS.slice(CSS.indexOf('[data-theme="dark"]'));
const light = tokensIn(CSS.slice(0, CSS.indexOf('[data-theme="dark"]')));
const dark = { ...light, ...tokensIn(darkBlock.slice(0, darkBlock.indexOf('}\n'))) };

function channel(value) {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  return (
    0.2126 * channel((n >> 16) & 255) +
    0.7152 * channel((n >> 8) & 255) +
    0.0722 * channel(n & 255)
  );
}

function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

/**
 * Every foreground/background pair the interface actually paints.
 *
 * Listed explicitly rather than generated as a cross product: most of the 200+
 * combinations never appear on screen, and a report full of irrelevant failures
 * is one nobody reads.
 */
const PAIRS = [
  // Body and secondary text on each surface a panel can sit on.
  ['ink', 'ground'], ['ink', 'surface'], ['ink', 'raised'],
  ['muted', 'ground'], ['muted', 'surface'], ['muted', 'raised'],
  ['faint', 'ground'], ['faint', 'surface'], ['faint', 'raised'],
  // The verdict triple, on its own tinted background and on the page.
  ['ok', 'ok-soft'], ['ok', 'ground'], ['ok', 'surface'],
  ['review', 'review-soft'], ['review', 'ground'], ['review', 'surface'],
  ['bad', 'bad-soft'], ['bad', 'ground'], ['bad', 'surface'],
  // The product accent, which carries no meaning but does carry text.
  ['accent', 'accent-soft'], ['accent', 'ground'], ['accent', 'surface'],
  // Brand chrome.
  ['brand-ink', 'brand'],
];

const FLOOR = 4.5;
let failures = 0;

for (const [themeName, theme] of [['light', light], ['dark', dark]]) {
  console.log(`\n  ${themeName}`);
  for (const [fg, bg] of PAIRS) {
    if (!theme[fg] || !theme[bg]) continue; // token not defined in this build
    const value = ratio(theme[fg], theme[bg]);
    const pass = value >= FLOOR;
    if (!pass) failures++;
    console.log(
      `    ${pass ? 'ok  ' : 'FAIL'} ${value.toFixed(2).padStart(5)}:1  ` +
        `${fg} on ${bg}  (${theme[fg]} / ${theme[bg]})`,
    );
  }
}

console.log(
  failures === 0
    ? `\n  all pairs clear ${FLOOR}:1\n`
    : `\n  ${failures} pair(s) below ${FLOOR}:1\n`,
);
process.exit(failures === 0 ? 0 : 1);
