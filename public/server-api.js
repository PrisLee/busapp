/* BusAPI backed by the Python server in busapp/server.py. */
window.BusAPI = (function () {
  "use strict";

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok && r.status !== 404) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  return {
    startupHint: "Start it with <code>python3 serve.py</code> and reload.",
    config: function () { return getJSON("/api/config"); },
    search: function (q) { return getJSON("/api/search?q=" + encodeURIComponent(q)); },
    plan: function (code) { return getJSON("/api/plan?dest=" + encodeURIComponent(code)); }
  };
})();
