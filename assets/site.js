/* ADA tanıtım sitesi: indirme bağlantılarını GitHub'daki sürümlerden günceller.
   İstek başarısız olursa sayfadaki sabit bağlantılar (sürümler sayfası) geçerli kalır.
   Çerez, izleyici ya da başka bir dış istek yoktur. */
(function () {
  "use strict";
  if (!window.fetch) return;

  var API = "https://api.github.com/repos/Guardscloud/ADA/releases?per_page=30";
  var controller = window.AbortController ? new AbortController() : null;
  var timer = setTimeout(function () { if (controller) controller.abort(); }, 8000);

  function all(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  function setText(sel, text) { all(sel).forEach(function (el) { el.textContent = text; }); }
  function setHref(sel, href) { all(sel).forEach(function (el) { el.href = href; }); }
  function findAsset(release, re) {
    for (var i = 0; i < release.assets.length; i++) {
      if (re.test(release.assets[i].name)) return release.assets[i];
    }
    return null;
  }

  fetch(API, { headers: { Accept: "application/vnd.github+json" }, signal: controller ? controller.signal : undefined })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (releases) {
      clearTimeout(timer);
      var win = null, mac = null;
      releases.forEach(function (rel) {
        if (rel.draft || rel.prerelease || !rel.assets) return;
        if (!win && findAsset(rel, /^ADA-Setup\.exe$/)) win = rel;
        if (!mac && findAsset(rel, /mac-arm64\.dmg$/)) mac = rel;
      });

      if (win) {
        setText("[data-win-version]", win.tag_name.replace(/^v/, ""));
      }
      if (mac) {
        var ver = mac.tag_name.replace(/^v/, "");
        var arm = findAsset(mac, /mac-arm64\.dmg$/);
        var intel = findAsset(mac, /mac-x86_64\.dmg$/);
        setText("[data-mac-version]", ver);
        if (arm) setHref("[data-mac-arm]", arm.browser_download_url);
        if (intel) setHref("[data-mac-intel]", intel.browser_download_url);
        all("[data-mac-ready]").forEach(function (el) { el.hidden = false; });
      }
    })
    .catch(function () { clearTimeout(timer); /* sabit bağlantılar kalır */ });
})();
