/* 공략 홈용 미사용 쿠폰 빨간 알림 표시 */
(() => {
  'use strict';
  const dot = document.querySelector('#couponNoticeDot');
  const link = document.querySelector('#couponShortcut');
  if (!dot || !link) return;
  const KEY = 'last_asylum_guide_used_coupons_v1';
  const unique = a => [...new Set((Array.isArray(a) ? a : []).filter(s => typeof s === 'string'))];
  let baseData = {codes:[],blocked:[]},sheetData = {codes:[],blocked:[]};
  const show = () => {
    const blocked = new Set([...unique(baseData.blocked),...unique(sheetData.blocked)].map(s => s.toUpperCase()));
    const current = unique([...unique(baseData.codes),...unique(sheetData.codes)]).filter(s => !blocked.has(s.toUpperCase()));
    let used = [];
    try { used = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (_) {}
    const usedSet = new Set(unique(used).map(s => s.toUpperCase()));
    const count = current.filter(s => !usedSet.has(s.toUpperCase())).length;
    dot.hidden = count === 0;
    link.setAttribute('aria-label', count ? `쿠폰코드, 미사용 ${count}개` : '쿠폰코드, 모두 사용 완료');
  };
  async function getFallback() {
    try {
      const r = await fetch('./codes.json?t=' + Date.now(), { cache: 'no-store' });
      if (r.ok) { baseData = await r.json(); show(); }
    } catch (_) { /* 오프라인일 때는 배지 숨김 */ }
  }
  getFallback();
  const endpoint = (window.LAST_ASYLUM_COUPON_ENDPOINT || '').trim();
  if (/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec$/.test(endpoint)) {
    const cb = '__lastAsylumHomeCoupons';
    const script = document.createElement('script');
    window[cb] = data => { if (data && Array.isArray(data.codes)) { sheetData=data; show(); } script.remove(); delete window[cb]; };
    script.onerror = () => { script.remove(); delete window[cb]; };
    script.src = endpoint + '?callback=' + cb + '&t=' + Date.now();
    document.head.append(script);
  }
})();
