/* Browser geometry regression check. Run against a static server at the repo root.
   PLAYWRIGHT_MODULE may point to an existing Playwright installation. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const output = process.argv[3] || '/tmp/prism-layout-check';

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const theme of ['light', 'dark']) {
      for (const width of [1440, 1280, 1024, 901, 900, 768, 600, 438, 375]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(base + '/design-system/preview/layout.html');
        if (theme === 'dark') await page.locator('#theme-toggle').click();
        await page.evaluate(() => document.fonts.ready);
        const geometry = await page.evaluate(() => {
          const rect = el => { const r = el.getBoundingClientRect(); return { left:r.left, right:r.right, width:r.width, height:r.height }; };
          const alignments = ['nested', 'direct', 'tile'].map(key => {
            const control = rect(document.querySelector('[data-align="' + key + '"]'));
            const table = rect(document.querySelector('[data-table="' + key + '"]'));
            return { key, left:Math.abs(control.left-table.left), right:Math.abs(control.right-table.right) };
          });
          const controls = [...document.querySelectorAll('.control-row')].flatMap(row => {
            const target = row.classList.contains('control-row--compact') ? 32 : 36;
            return [...row.querySelectorAll('input.field,select.field,.ds-field,.ds-btn,.selctl,.srcfilter__chip')].map(el => ({ target:el.matches('.field') && el.closest('.selctl') ? target-2 : target, height:rect(el).height }));
          });
          const stats = [...document.querySelectorAll('#stats>.tile')].map(rect);
          const grid = document.querySelector('#stats');
          const comparison = document.querySelector('#comparison');
          const cells = [...comparison.querySelectorAll('thead th')].slice(1).map(rect);
          const scroll = document.querySelector('#compare-scroll');
          const two = [...document.querySelector('#two-column').children].map(rect);
          const summary = document.querySelector('#summary-section');
          const detail = document.querySelector('#detail-section');
          const noteStyle = getComputedStyle(summary.querySelector('.result-notes'));
          const detailStyle = getComputedStyle(detail);
          const sections = {
            gap:detail.getBoundingClientRect().top-summary.getBoundingClientRect().bottom,
            padding:parseFloat(detailStyle.paddingTop),
            divider:parseFloat(detailStyle.borderTopWidth),
            noteSize:parseFloat(noteStyle.fontSize),
            noteLineHeight:parseFloat(noteStyle.lineHeight)
          };
          return { alignments, controls, stats, cells, two, sections,
            gridOverflow:grid.scrollWidth-grid.clientWidth,
            pageOverflow:document.documentElement.scrollWidth-innerWidth,
            tableOverflow:scroll.scrollWidth-scroll.clientWidth };
        });
        for (const a of geometry.alignments) assert.ok(a.left <= 1 && a.right <= 1, `${width}/${theme}: ${a.key} alignment ${JSON.stringify(a)}`);
        for (const c of geometry.controls) assert.ok(Math.abs(c.height-c.target) <= 1, `${width}/${theme}: control ${JSON.stringify(c)}`);
        assert.ok(geometry.pageOverflow <= 1, `${width}/${theme}: page overflow ${geometry.pageOverflow}`);
        assert.ok(geometry.gridOverflow <= 1, `${width}/${theme}: grid overflow`);
        assert.ok(geometry.sections.gap >= 24, `${width}/${theme}: distinct sections need at least 24px separation`);
        assert.ok(geometry.sections.padding >= 20 && geometry.sections.divider >= 1, `${width}/${theme}: missing section boundary`);
        assert.ok(geometry.sections.noteSize >= 13 && geometry.sections.noteLineHeight >= 20, `${width}/${theme}: result notes too small or dense`);
        for (const tile of geometry.stats) assert.ok(tile.width >= 139, `${width}/${theme}: tile too narrow`);
        assert.ok(Math.max(...geometry.cells.map(c=>c.width))-Math.min(...geometry.cells.map(c=>c.width)) <= 1, 'Model columns must have equal widths');
        if (width <= 1100) assert.ok(Math.abs(geometry.two[0].left-geometry.two[1].left) <= 1, 'Narrow two-column layout should stack');
        for (const count of ['2', '4', '6']) {
          await page.locator('#model-count').selectOption(count);
          const widths = await page.locator('#comparison thead th').evaluateAll(cells=>cells.slice(1).map(el=>el.getBoundingClientRect().width));
          assert.equal(widths.length, Number(count));
          assert.ok(Math.max(...widths)-Math.min(...widths) <= 1, `${count} model widths`);
        }
        await page.screenshot({ path:path.join(output, `layout-${theme}-${width}.png`), fullPage:true, animations:'disabled' });
        results.push({ theme, width, ...geometry });
      }
    }
    await page.locator('input[aria-label="표본 검색"]').focus();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#theme-toggle').evaluate(el=>el.matches(':focus-visible')), true);
    assert.equal(await page.locator('button[disabled]').isDisabled(), true);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'measurements.json'), JSON.stringify(results, null, 2));
    console.log(`PASS: ${results.length} viewport/theme combinations; alignment, controls, grid, model columns, focus and disabled state.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
