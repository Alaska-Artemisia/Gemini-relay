/* Me + Lia — site-wide source capture for Klaviyo onsite forms (popup, footer).
   Mirrors the lead-magnet landing handler so every signup path tags `channel`.
   The landing page (page.slow-wardrobe-landing) uses {% layout none %} and does
   not load theme.liquid, so it keeps its own handler — no double-fire here. */
(function () {
  var UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];

  function readUtms() {
    var p = new URLSearchParams(window.location.search), o = {};
    UTM_KEYS.forEach(function (k) { var v = p.get(k); if (v) { o[k] = v; } });
    return o;
  }

  function persistFirstTouch() {
    try {
      var u = readUtms();
      if (Object.keys(u).length && !sessionStorage.getItem('ml_utms')) {
        sessionStorage.setItem('ml_utms', JSON.stringify(u));
        sessionStorage.setItem('ml_landing', window.location.href);
      }
    } catch (e) {}
  }

  function getUtms() {
    var u = readUtms();
    if (Object.keys(u).length) { return u; }
    try { var r = sessionStorage.getItem('ml_utms'); return r ? JSON.parse(r) : {}; }
    catch (e) { return {}; }
  }

  function deriveChannel(src) {
    if (!src) { return 'Direct'; }
    var s = String(src).toLowerCase();
    if (s === 'facebook' || s === 'instagram' || s === 'fb' || s === 'ig' || s === 'meta') { return 'Meta'; }
    if (s === 'pinterest' || s === 'pin') { return 'Pinterest'; }
    if (s === 'google') { return 'Google'; }
    return src;
  }

  persistFirstTouch();

  // When any Klaviyo onsite form (popup WpgHez, footer V5wrtd, etc.) is submitted,
  // stamp the same UTM + channel properties onto the active profile.
  window.addEventListener('klaviyoForms', function (e) {
    if (!e.detail || e.detail.type !== 'submit') { return; }
    var u = getUtms(), props = {};
    UTM_KEYS.forEach(function (k) { if (u[k]) { props[k] = u[k]; } });
    props.channel = deriveChannel(u.utm_source);
    try { var lp = sessionStorage.getItem('ml_landing'); if (lp) { props.landing_url = lp; } } catch (e) {}
    var kl = window.klaviyo || (window.klaviyo = []);
    try { kl.push(['identify', props]); } catch (err) {}
  });
})();
