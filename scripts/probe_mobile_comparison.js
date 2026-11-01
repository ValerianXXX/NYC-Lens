(expected) => {
  // Read-only browser probe. Supply a visible, completed comparison; do not
  // fetch APIs, call application functions, or mutate application/browser state.
  const result = document.querySelector('#comparison-result');
  const table = result.querySelector('.comparison-table');
  const mobile = result.querySelector('.comparison-mobile');
  const visible = (node) => Boolean(node && node.getClientRects().length &&
    getComputedStyle(node).visibility !== 'hidden');
  const text = (node) => node ? node.textContent.trim().replace(/\s+/g, ' ') : null;
  const items = [...result.querySelectorAll('.comparison-item')].map((item) => ({
    metric: item.dataset.metricId, site: item.dataset.siteId,
    presentation: item.closest('.comparison-mobile') ? 'mobile' : 'table',
    visible: visible(item), value: text(item.querySelector('.metric-value')),
    rank: text(item.querySelector('.metric-rank')),
    evidence: item.querySelector('a')?.getAttribute('href') || null,
    detail: text(item.querySelector('details')),
  }));
  const pairs = new Map();
  for (const item of items) {
    const key = item.metric + '|' + item.site;
    if (!pairs.has(key)) pairs.set(key, {});
    pairs.get(key)[item.presentation] = item;
  }
  const mismatches = [...pairs].filter(([,pair]) => !pair.mobile || !pair.table ||
    ['value','rank','evidence','detail'].some(key=>pair.mobile[key]!==pair.table[key])).map(([key])=>key);
  const narrow = innerWidth <= 620;
  const checks = {
    groupedMobilePresentation: narrow ? visible(mobile) && !visible(table) : visible(table) && !visible(mobile),
    sameFourteenBoundValues: pairs.size === expected.items && items.length === expected.items * 2 && !mismatches.length,
    noHorizontalMobileResultOverflow: !narrow || result.scrollWidth <= result.clientWidth + 1,
    noDuplicateVisibleValues: items.filter(item=>item.visible).length === expected.items,
    sevenMetricGroups: Boolean(mobile && mobile.querySelectorAll('.comparison-metric-card').length === expected.metrics),
    actualReportLinks: document.querySelectorAll('#comparison-exports a[href*="/report?format="]').length === 3,
  };
  return {
    viewport: {width:innerWidth,height:innerHeight}, language:document.documentElement.lang,
    resultWidth:result.clientWidth, resultScrollWidth:result.scrollWidth,
    documentOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,
    checks, passed:Object.values(checks).every(Boolean), items, mismatches,
    comparisonHref:document.querySelector('#comparison-exports a')?.getAttribute('href') || null,
    job:document.querySelector('#job-identity').textContent,
  };
};
