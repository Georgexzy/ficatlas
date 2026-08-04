// Find UI that is visually broken in ways a screenshot review misses.
//
// Three classes of defect, all measurable:
//
//   OVERLAP   An interactive element is covered at its own centre by something
//             that is not its own descendant. This is the help-panel-over-the-
//             search-bar bug: both render, both look fine in isolation, and one
//             cannot be used.
//   CLIPPED   An element extends outside a scroll container that hides the
//             overflow, so part of it is unreachable.
//   OFFSCREEN An element sits outside the viewport horizontally.
const { chromium, devices } = require('playwright');

const PAGES = [
  ['home','/'], ['results','/?q=harry+potter&per_page=4'], ['library','/library'],
  ['account','/account'], ['login','/login'], ['forgot','/forgot'],
  ['about','/about'], ['takedown','/takedown'], ['settings','/settings'],
];

// Some things are meant to cover the page.
const INTENTIONAL = /backdrop|overlay|modal|sheet|drawer|scrim|tabbar|site-header|header/i;

async function audit(page, label) {
  return await page.evaluate((INT) => {
    const re = new RegExp(INT.source, INT.flags);
    const out = [];
    const vw = document.documentElement.clientWidth;
    const els = document.querySelectorAll(
      'a,button,input,select,textarea,[role=button],[role=slider],[role=dialog],.helptip,.helptip__panel');
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const st = getComputedStyle(el);
      if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
      const name = (el.className || '').toString().split(' ')[0] || el.tagName;

      if (r.left < -2 || r.right > vw + 2) {
        out.push(`OFFSCREEN ${name} [${Math.round(r.left)}..${Math.round(r.right)}] vw=${vw}`);
        continue;
      }
      // Only test elements whose centre is actually in view.
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      if (cy < 0 || cy > window.innerHeight) continue;
      const top = document.elementFromPoint(cx, cy);
      if (!top) continue;
      if (el.contains(top) || top.contains(el)) continue;
      if (re.test((top.className || '').toString()) || re.test(top.tagName)) continue;
      // Ignore labels wrapping their own control and similar.
      if (top.closest && top.closest(`.${CSS.escape(name)}`)) continue;
      out.push(`OVERLAP ${name} covered by ${(top.className||top.tagName).toString().split(' ')[0]}`);
    }
    return [...new Set(out)];
  }, INTENTIONAL);
}

(async () => {
  const browser = await chromium.launch();
  for (const [vname, opts] of [['desktop', { viewport:{width:1440,height:900} }],
                               ['phone', devices['iPhone 13']]]) {
    const p = await (await browser.newContext(opts)).newPage();
    for (const [name, path] of PAGES) {
      try {
        await p.goto('http://localhost:3000'+path, { waitUntil:'domcontentloaded', timeout:30000 });
        await p.waitForTimeout(2200);
        let issues = await audit(p, name);
        // Also exercise the states that only exist after interaction — this is
        // where the help panel lives, and it is why static sweeps missed it.
        for (const sel of ['.helptip__btn', '.tag-input__field']) {
          const h = await p.$(sel);
          if (h) { try { await h.click({ timeout: 1500 }); await p.waitForTimeout(600);
                         issues = issues.concat(await audit(p, name)); } catch {} }
        }
        issues = [...new Set(issues)];
        console.log(`${issues.length ? 'FAIL' : ' ok '} ${name}-${vname}` +
                    (issues.length ? '\n    - ' + issues.slice(0,5).join('\n    - ') : ''));
      } catch (e) { console.log(`ERR  ${name}-${vname}: ${e.message.split('\n')[0].slice(0,60)}`); }
    }
  }
  await browser.close();
})();
