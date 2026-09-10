/* busapp front end. No framework, no build step. */
(function () {
  "use strict";

  var cfg = null;
  var current = null;          // last plan response
  var clockSkew = 0;           // serverNow - browserNow, so countdowns follow the server
  var refreshTimer = null;
  var searchTimer = null;
  var activeIndex = -1;
  var matches = [];

  var $ = function (id) { return document.getElementById(id); };
  var input = $("dest"), list = $("dest-list"), result = $("result");

  // --- tiny helpers --------------------------------------------------------
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function serverNow() { return Date.now() / 1000 + clockSkew; }
  function minsUntil(epoch) { return Math.round((epoch - serverNow()) / 60); }
  function plural(n, one, many) { return n + " " + (Math.abs(n) === 1 ? one : many); }

  function remember(stop) {
    try { localStorage.setItem("busapp.dest", JSON.stringify(stop)); } catch (e) { /* private mode */ }
  }
  function recall() {
    try { return JSON.parse(localStorage.getItem("busapp.dest") || "null"); } catch (e) { return null; }
  }

  // Data source. window.BusAPI is supplied by either server-api.js (talks to the
  // Python backend) or static-api.js (computes everything in the browser).
  var API = window.BusAPI;

  // --- startup -------------------------------------------------------------
  function boot() {
    API.config().then(function (c) {
      cfg = c;
      clockSkew = c.now - Date.now() / 1000;
      renderChrome();
      var last = recall();
      if (last && last.code) {
        input.value = last.name;
        $("clear").hidden = false;
        loadPlan(last, true);
      }
    }).catch(function (e) {
      result.appendChild(notice("Could not load the app",
        (API && API.startupHint ? API.startupHint : "Reload the page.")
        + " (" + e.message + ")"));
    });
  }

  function renderChrome() {
    $("origin-name").textContent = cfg.origin.name;
    $("origin-address").textContent = cfg.origin.address;

    $("intro-reach").textContent =
      "We can answer for " + cfg.direct_stops + " stops reachable without changing bus.";

    var cov = cfg.coverage;
    if (cfg.static_page) {
      $("intro-honesty").textContent =
        "On this demo page the " + cov.arrivals + " arrivals behind the margins were generated "
        + "in your browser, not measured. The real app logs them from LTA DataMall over weeks, "
        + "and refuses to quote a margin until a stop and hour has at least "
        + cfg.min_for_claim + " of them.";
      return;
    }
    $("intro-honesty").textContent = cov.arrivals >= cfg.min_for_claim
      ? "So far we have logged " + cov.arrivals + " real arrivals. A margin is only quoted once a "
        + "stop and hour has at least " + cfg.min_for_claim + " of them; below that the app says so."
      : "We have logged " + cov.arrivals + " arrivals so far, which is not yet enough to quote a "
        + "measured margin. Until it is, the app uses a flat " + cfg.default_margin_min
        + "-minute margin and tells you that is what it is doing.";

    if (cfg.static_page) {
      banner("Demo only. This page has no server, so every bus and every statistic on it "
             + "is invented. Run it locally with a free LTA DataMall key for real arrivals.");
    } else if (cfg.mode === "simulated") {
      banner("Demo mode: these buses are simulated, not real. Set DATAMALL_API_KEY "
             + "and restart to use live LTA arrivals.");
    } else if (cfg.demo_history) {
      banner("Live arrivals, but the history behind the margins is seeded demo data. "
             + "Run scripts/seed_demo_history.py --clear to remove it.");
    }

    var chips = $("chips");
    (cfg.suggestions || []).forEach(function (s) {
      var b = el("button", "chip", s.name);
      b.type = "button";
      b.addEventListener("click", function () {
        input.value = s.name;
        $("clear").hidden = false;
        closeList();
        loadPlan(s);
      });
      chips.appendChild(b);
    });

    updateStatus();
  }

  function banner(text) {
    var b = $("banner");
    b.textContent = text;
    b.hidden = false;
  }

  function updateStatus() {
    if (!cfg) return;
    var cov = cfg.coverage;
    var bits = [
      cfg.mode === "live" ? "Live LTA data" : "Simulated data",
      cov.arrivals + " arrivals logged",
      "polling " + cfg.poll_stops.join(", ")
    ];
    if (cov.last_poll) {
      var age = Math.max(0, Math.round(serverNow() - cov.last_poll));
      bits.push("last poll " + (age < 90 ? age + "s ago" : Math.round(age / 60) + " min ago"));
    }
    if (cov.last_poll_error) bits.push("last error: " + cov.last_poll_error);
    $("status").textContent = bits.join(" · ");
  }

  // --- search --------------------------------------------------------------
  input.addEventListener("input", function () {
    $("clear").hidden = !input.value;
    clearTimeout(searchTimer);
    var q = input.value.trim();
    if (q.length < 2) { closeList(); return; }
    searchTimer = setTimeout(function () { runSearch(q); }, 140);
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (list.hidden || !matches.length) return;
      e.preventDefault();
      activeIndex += (e.key === "ArrowDown" ? 1 : -1);
      if (activeIndex < 0) activeIndex = matches.length - 1;
      if (activeIndex >= matches.length) activeIndex = 0;
      paintActive();
    } else if (e.key === "Enter") {
      if (!list.hidden && matches[activeIndex]) {
        e.preventDefault();
        choose(matches[activeIndex]);
      } else if (matches.length) {
        e.preventDefault();
        choose(matches[0]);
      }
    } else if (e.key === "Escape") {
      closeList();
    }
  });

  $("clear").addEventListener("click", function () {
    input.value = "";
    $("clear").hidden = true;
    closeList();
    result.innerHTML = "";
    current = null;
    stopRefresh();
    input.focus();
  });

  document.addEventListener("click", function (e) {
    if (!list.contains(e.target) && e.target !== input) closeList();
  });

  function runSearch(q) {
    API.search(q).then(function (data) {
      if (input.value.trim() !== q) return;   // a newer keystroke won
      matches = data.results || [];
      activeIndex = matches.length ? 0 : -1;
      paintList(q);
    }).catch(function () { closeList(); });
  }

  function paintList(q) {
    list.innerHTML = "";
    if (!matches.length) {
      var none = el("li", "empty", "No bus stop matches “" + q + "”");
      none.setAttribute("role", "presentation");
      list.appendChild(none);
      open();
      return;
    }
    matches.forEach(function (m, i) {
      var li = el("li");
      li.id = "opt-" + i;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", i === activeIndex ? "true" : "false");

      var left = el("div");
      left.appendChild(el("div", "opt-name", m.name));
      left.appendChild(el("div", "opt-road", m.road || m.code));
      li.appendChild(left);

      li.appendChild(m.direct
        ? el("span", "opt-svc", "Bus " + m.services.join(", "))
        : el("span", "opt-none", "needs a transfer"));

      li.addEventListener("click", function () { choose(m); });
      list.appendChild(li);
    });
    open();
  }

  function paintActive() {
    Array.prototype.forEach.call(list.children, function (li, i) {
      li.setAttribute("aria-selected", i === activeIndex ? "true" : "false");
    });
    input.setAttribute("aria-activedescendant", activeIndex >= 0 ? "opt-" + activeIndex : "");
    var active = list.children[activeIndex];
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
  }

  function open() {
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    paintActive();
  }
  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-activedescendant", "");
  }

  function choose(stop) {
    input.value = stop.name;
    $("clear").hidden = false;
    closeList();
    loadPlan(stop);
  }

  // --- plan ----------------------------------------------------------------
  function loadPlan(stop, quiet) {
    remember(stop);
    if (!quiet) {
      result.innerHTML = "";
      result.appendChild(el("p", "spinner", "Checking the buses…"));
    }
    result.setAttribute("aria-busy", "true");
    API.plan(stop.code).then(function (data) {
      result.setAttribute("aria-busy", "false");
      if (data.error) {
        result.innerHTML = "";
        result.appendChild(notice("We don't know that stop", "Try searching again."));
        return;
      }
      current = data;
      clockSkew = data.now - Date.now() / 1000;
      renderPlan(data);
      startRefresh(stop);
      refreshCoverage();
    }).catch(function (e) {
      result.setAttribute("aria-busy", "false");
      result.innerHTML = "";
      result.appendChild(notice("Could not load arrivals", e.message));
    });
  }

  function startRefresh(stop) {
    stopRefresh();
    refreshTimer = setInterval(function () {
      if (document.hidden) return;              // don't poll a backgrounded tab
      API.plan(stop.code).then(function (data) {
        if (data.error) return;
        current = data;
        clockSkew = data.now - Date.now() / 1000;
        renderPlan(data);
      }).catch(function () { /* keep the last good answer on screen */ });
    }, 30000);
  }
  function stopRefresh() { if (refreshTimer) clearInterval(refreshTimer); refreshTimer = null; }

  function refreshCoverage() {
    API.config().then(function (c) { cfg = c; updateStatus(); }).catch(function () {});
  }

  function notice(title, html) {
    var n = el("div", "notice");
    n.appendChild(el("h2", null, title));
    var p = el("p");
    p.innerHTML = html;
    n.appendChild(p);
    return n;
  }

  function renderPlan(data) {
    result.innerHTML = "";

    var head = el("div", "dest-head");
    var h = el("h2", null, "To " + data.destination.name);
    head.appendChild(h);
    var link = el("a", "muted", "View stop on BusRouter SG ↗");
    link.href = data.destination.busrouter_url;
    link.target = "_blank";
    link.rel = "noopener";
    head.appendChild(link);
    result.appendChild(head);

    if (!data.reachable) {
      result.appendChild(notice(
        "No direct bus from CT Hub 2",
        "None of the " + cfg.services.length + " services outside the building reach "
        + data.destination.name + " without a change. This app only keeps a track record for "
        + "direct rides, so it would be guessing. Plan the transfer on "
        + "<a href=\"" + data.destination.busrouter_url + "\" target=\"_blank\" rel=\"noopener\">BusRouter SG</a>."));
      return;
    }

    var withBus = data.options.filter(function (o) { return o.target; });
    if (!withBus.length) {
      var why = data.live_error
        ? "We could not reach the arrivals feed just now (" + data.live_error + ")."
        : "Nothing is being tracked on " + data.options.map(function (o) { return o.service; }).join(", ")
          + " right now — most likely outside service hours.";
      result.appendChild(notice("No bus to catch at the moment", why));
      return;
    }

    result.appendChild(card(withBus[0], true));

    var rest = withBus.slice(1);
    if (rest.length) {
      var det = el("details", "alts");
      var sum = el("summary", null, plural(rest.length, "other option", "other options")
        + " on " + rest.map(function (o) { return o.service; }).join(", "));
      det.appendChild(sum);
      rest.forEach(function (o) { det.appendChild(card(o, false)); });
      result.appendChild(det);
    }
  }

  function card(o, primary) {
    var c = el("div", "card " + (primary ? "primary" : "alt"));
    var t = o.target;
    var leaveIn = minsUntil(t.leave_epoch);

    c.appendChild(el("p", "leave-label", primary ? "Leave your desk" : "Leave"));

    var time = el("p", "leave-time" + (leaveIn <= 0 ? " now" : ""),
                  leaveIn <= 0 ? "Now" : t.leave_hhmm);
    c.appendChild(time);

    var inLine = el("p", "leave-in" + (leaveIn <= 2 ? " urgent" : ""),
      leaveIn <= 0
        ? "You should already be at the lift — leave at " + t.leave_hhmm
        : "in " + plural(leaveIn, "minute", "minutes"));
    c.appendChild(inLine);

    // Which bus, from where.
    var line = el("div", "svc-line");
    line.appendChild(el("span", "svc-badge", o.service));
    var txt = el("span", "svc-text");
    txt.innerHTML = "arrives <strong>" + t.eta_hhmm + "</strong> ("
      + plural(t.in_min, "min", "min") + ") at " + escapeHTML(o.board_name)
      + " · " + plural(o.walk_min, "min", "min") + " walk";
    line.appendChild(txt);
    c.appendChild(line);

    // The arithmetic, itemised. Trust comes from showing the working, and the
    // lift wait is an assumption rather than a measurement, so it gets its own row.
    c.appendChild(calc(o, t));

    // Pills: load, distance, confidence.
    var pills = el("div", "pills");
    var bus0 = o.buses[0];
    if (bus0 && bus0.load_label) {
      pills.appendChild(el("span", "pill " + (bus0.load === "SEA" ? "good" : bus0.load === "LSD" ? "warn" : ""),
                           bus0.load_label));
    }
    pills.appendChild(el("span", "pill", plural(o.hops, "stop", "stops") + " to go"));
    pills.appendChild(el("span", "pill " + confClass(o.history),
      o.history.claimable
        ? "Measured from " + plural(o.history.n, "arrival", "arrivals")
        : "No track record yet"));
    if (o.terminus) pills.appendChild(el("span", "pill", "towards " + o.terminus));
    c.appendChild(pills);

    (o.signals || []).forEach(function (s) {
      c.appendChild(el("p", "signal", s.text));
    });

    // The cost of missing it.
    if (o.history.wait_typical_s != null) {
      var m = el("p", "miss");
      m.innerHTML = "Miss it and the next " + o.service + " usually comes in <strong>"
        + Math.round(o.history.wait_typical_s / 60) + " min</strong>, up to <strong>"
        + Math.round(o.history.wait_p90_s / 60) + " min</strong> on a bad day.";
      c.appendChild(m);
    } else if (o.buses.length > 1) {
      var m2 = el("p", "miss");
      m2.innerHTML = "Miss it and the one behind is due <strong>" + o.buses[1].eta_hhmm
        + "</strong> (" + plural(o.buses[1].in_min, "min", "min") + ").";
      c.appendChild(m2);
    }

    if (o.history.claimable) {
      c.appendChild(el("p", "basis", "Based on " + o.history.n + " arrivals logged at "
        + o.board_name + ", " + o.history.basis_label + "."));
    } else {
      c.appendChild(el("p", "basis", "Only " + o.history.n_total + " arrivals logged at "
        + o.board_name + " so far — " + cfg.min_for_claim
        + " are needed before this app will quote a measured margin."));
    }
    return c;
  }

  function calcRow(label, value, cls) {
    var li = el("li", cls || null);
    li.appendChild(el("span", "calc-label", label));
    li.appendChild(el("span", "calc-val", value));
    return li;
  }

  function calc(o, t) {
    var wrap = el("div", "calc-wrap");
    var ul = el("ul", "calc");
    ul.appendChild(calcRow("Bus " + o.service + " due at " + o.board_name, t.eta_hhmm, "calc-start"));
    if (t.margin_min > 0) {
      ul.appendChild(calcRow(
        t.margin_is_default ? "Margin in case it comes early (default)"
                            : "Margin — how early " + o.service + " actually runs",
        "− " + plural(t.margin_min, "min", "min")));
    }
    ul.appendChild(calcRow("Walk to the stop (" + o.walk_min * 80 + "m)",
                           "− " + plural(t.walk_min, "min", "min")));
    if (t.exit_min > 0) {
      ul.appendChild(calcRow("Lift and out of the building",
                             "− " + plural(t.exit_min, "min", "min"), "calc-lift"));
    }
    ul.appendChild(calcRow("Leave your desk", t.leave_hhmm, "calc-total"));
    wrap.appendChild(ul);

    var note = el("p", "calc-note");
    note.innerHTML = t.margin_is_default
      ? "The margin is a flat " + t.margin_min + "-minute default — there is not enough "
        + "history at this stop yet to measure it. The lift allowance is a fixed assumption."
      : "The margin is measured: in " + Math.round(o.history.quantile * 100)
        + "% of logged arrivals, bus " + o.service + " turned up no more than this much "
        + "earlier than first predicted. The lift allowance is a fixed assumption.";
    wrap.appendChild(note);
    return wrap;
  }

  function confClass(h) {
    if (!h.claimable) return "warn";
    return h.confidence === "good" ? "good" : "";
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  // Keep countdowns honest between server refreshes.
  setInterval(function () {
    if (current && !document.hidden) { renderPlan(current); updateStatus(); }
  }, 15000);

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && current) renderPlan(current);
  });

  if (!API) {
    result.appendChild(notice("No data source loaded",
      "The page needs server-api.js or static-api.js before app.js."));
  } else {
    boot();
  }
})();
