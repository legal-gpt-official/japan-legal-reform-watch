/*
 * Cloudflare Web Analytics loader (approved 2026-10-06).
 *
 * Cookieless page-view counting for the owner's daily access email
 * (scripts/send_access_report.py). It is the dashboard's only third-party
 * script: it loads nothing until a site token is set below, accepts no other
 * host, and runs with SPA tracking off so filter/URL-state changes are not
 * counted as extra page views. The token is public by design (Cloudflare
 * embeds it in every page that uses the beacon); it is not a secret.
 *
 * When the token changes, bump the analytics.js ?v= cache buster in
 * docs/index.html and docs/alerts/thank-you.html.
 */
(function () {
  "use strict";

  var CLOUDFLARE_WEB_ANALYTICS_TOKEN = "";
  var BEACON_SRC = "https://static.cloudflareinsights.com/beacon.min.js";

  if (!/^[0-9a-f]{32}$/.test(CLOUDFLARE_WEB_ANALYTICS_TOKEN)) return;

  var script = document.createElement("script");
  script.defer = true;
  script.src = BEACON_SRC;
  script.setAttribute(
    "data-cf-beacon",
    JSON.stringify({ token: CLOUDFLARE_WEB_ANALYTICS_TOKEN, spa: false })
  );
  document.head.appendChild(script);
})();
