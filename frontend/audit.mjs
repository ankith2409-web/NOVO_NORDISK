/**
 * The checks a unit test cannot make.
 *
 * Vitest renders components; it cannot tell you that secondary text sits at
 * 3.14:1 against the surface it is actually painted on, that a button shows
 * the wrong cursor, or that a provider error's billing URL runs out through
 * the side of the panel. Every one of those was a real defect here, and each
 * was found by this file rather than by review.
 *
 * Run against a live server:
 *   concordance serve <model> --port 8959
 *   node audit.mjs                     # or CONCORDANCE_URL=... node audit.mjs
 *
 * The copilot section spends real LLM quota. It exercises the failure path
 * just as usefully when a provider is exhausted, which is how the overflow bug
 * surfaced.
 */
import { chromium } from 'playwright';
const URL = process.env.CONCORDANCE_URL ?? 'http://127.0.0.1:8959/';
// Pinned only when the sandbox provides a browser at a fixed path; falls back
// to whatever Playwright resolves normally.
const executablePath = process.env.PLAYWRIGHT_CHROMIUM || undefined;
const browser = await chromium.launch(executablePath ? { executablePath } : {});
const fails = [];
const ok = (m) => console.log('  ✓ ' + m);

async function contrastScan(page) {
  return page.evaluate(() => {
    const lum = (c) => { const [r,g,b]=c.match(/[\d.]+/g).map(Number).map(v=>{v/=255;return v<=0.03928?v/12.92:((v+0.055)/1.055)**2.4}); return 0.2126*r+0.7152*g+0.0722*b; };
    const out = [];
    for (const el of document.querySelectorAll('p,h1,h2,h3,span,code,li,button,label')) {
      const text = el.textContent.trim();
      if (!text || el.children.length) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none') continue;
      const size = parseFloat(s.fontSize);
      let b = s.backgroundColor, n = el;
      while (b === 'rgba(0, 0, 0, 0)' && n.parentElement) { n = n.parentElement; b = getComputedStyle(n).backgroundColor; }
      const L1 = lum(s.color), L2 = lum(b);
      const ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
      const needs = size >= 24 || (size >= 18.66 && parseInt(s.fontWeight) >= 700) ? 3 : 4.5;
      if (ratio < needs) out.push(`${size}px ${ratio.toFixed(2)}<${needs} "${text.slice(0,34)}"`);
    }
    return out;
  });
}

for (const theme of ['light', 'dark']) {
  console.log(`\n── ${theme} theme ──`);
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: theme });
  page.on('pageerror', e => fails.push(`[${theme}] PAGE ERROR ${e.message}`));
  page.on('console', m => { if (m.type()==='error') fails.push(`[${theme}] CONSOLE ${m.text()}`); });
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.evaluate(t => { document.documentElement.dataset.theme = t; localStorage.setItem('concordance-theme', t); }, theme);
  const start = page.getByRole('button', { name: 'start' });
  if (await start.count()) await start.click();
  await page.waitForTimeout(150);

  // buttons: enabled ones must be pointer + named + tall enough
  for (const b of await page.locator('button:visible').all()) {
    const [cursor, box, name, disabled] = await Promise.all([
      b.evaluate(el => getComputedStyle(el).cursor), b.boundingBox(),
      b.evaluate(el => (el.getAttribute('aria-label') || el.textContent || '').trim()),
      b.isDisabled(),
    ]);
    if (!name) fails.push(`[${theme}] unnamed button`);
    if (!disabled && cursor !== 'pointer') fails.push(`[${theme}] cursor ${cursor} on "${name}"`);
    if (disabled && cursor !== 'not-allowed') fails.push(`[${theme}] disabled button lacks not-allowed: "${name}"`);
    if (box && box.height < 28) fails.push(`[${theme}] ${box.height}px button "${name}"`);
  }
  ok('buttons: cursor, name, size');

  // walk every view and scan contrast in each
  for (const view of ['Overview','Model','Requirements','Drift','Review']) {
    const nav = page.getByRole('button', { name: view, exact: true });
    if (await nav.count()) { await nav.click(); await page.waitForTimeout(450); }
    const bad = await contrastScan(page);
    if (bad.length) bad.slice(0,4).forEach(c => fails.push(`[${theme}/${view}] CONTRAST ${c}`));
  }
  ok('contrast across 5 views');
  await page.close();
}

// ── the chat, end to end ──
console.log('\n── copilot ──');
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', e => fails.push('CHAT PAGE ERROR ' + e.message));
await page.goto(URL, { waitUntil: 'networkidle' });
const s = page.getByRole('button', { name: 'start' });
if (await s.count()) await s.click();

const composer = page.getByRole('textbox', { name: /Ask about this model/i });
const send = page.getByRole('button', { name: /Send question/i });

if (!(await send.isDisabled())) fails.push('send enabled with empty composer');
else ok('send disabled while composer empty');

// clickable opener fills the composer
await page.getByRole('button', { name: /Which joins are inactive/ }).click();
if (!(await composer.inputValue()).includes('joins')) fails.push('opener did not fill composer');
else ok('suggested opener fills composer');
if (await send.isDisabled()) fails.push('send still disabled with text'); else ok('send enables with text');

// Shift+Enter must NOT submit
await composer.fill('line one');
await composer.press('Shift+Enter');
await composer.type('line two');
if (!(await composer.inputValue()).includes('\n')) fails.push('Shift+Enter did not insert a newline');
else ok('Shift+Enter inserts newline, does not send');

// real question -> optimistic echo -> thinking -> answer
await composer.fill('How many measures are in this model?');
await composer.press('Enter');
const echoed = await page.getByText('How many measures are in this model?').first().isVisible().catch(()=>false);
if (!echoed) fails.push('question not echoed into thread immediately');
else ok('question echoes into the thread at once');
const thinking = await page.getByText('Thinking…').isVisible().catch(()=>false);
if (!thinking) fails.push('no thinking indicator'); else ok('thinking indicator shown');
if (await composer.inputValue() !== '') fails.push('composer not cleared'); else ok('composer cleared');

await page.waitForSelector('text=Thinking…', { state: 'detached', timeout: 45000 });
const log = page.getByRole('log');
const answerText = (await log.innerText()).trim();
if (answerText.length < 60) fails.push('answer looks empty: ' + answerText.slice(0,80));
else ok('answer rendered (' + answerText.length + ' chars)');
const chips = await log.locator('span').filter({ hasText: /^(describe_|list_|what_|overview)/ }).count();
ok(`tool chips shown: ${chips}`);
await page.screenshot({ path: '/tmp/shot-chat.png' });

// nothing may overflow its own container -- document-level scroll checks miss
// this entirely, which is how a provider error's billing URL ran out through
// the side of the copilot panel unnoticed.
const overflow = await page.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll('p, span, li, h1, h2, code, div')) {
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible') {
      const txt = el.textContent.trim().slice(0, 40);
      if (txt) out.push(`${el.tagName} +${el.scrollWidth - el.clientWidth}px "${txt}"`);
    }
  }
  return out;
});
if (overflow.length) overflow.slice(0,4).forEach(o => fails.push('OVERFLOW ' + o));
else ok('no element overflows its container');

// reduced motion: nothing may animate
const rm = await browser.newPage({ viewport:{width:1440,height:900}, reducedMotion:'reduce' });
await rm.goto(URL, { waitUntil:'networkidle' });
const animating = await rm.evaluate(() => [...document.querySelectorAll('*')].filter(el => getComputedStyle(el).animationName !== 'none').length);
if (animating) fails.push(`${animating} elements still animating under prefers-reduced-motion`);
else ok('reduced motion: no animations run');
await rm.close();

console.log('\n' + (fails.length ? '✗ FAILURES:\n' + fails.map(f=>'  '+f).join('\n') : '✓ ALL CHECKS PASSED'));
await browser.close();
process.exit(fails.length ? 1 : 0);
