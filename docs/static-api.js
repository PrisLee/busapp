/* BusAPI with no backend at all: the simulator, the statistics and the planner,
 * running entirely in the browser so the app can be published on GitHub Pages.
 *
 * This is a faithful port of busapp/datamall.py, stats.py and planner.py. It
 * exists because GitHub Pages serves static files only -- there is no process to
 * hold an API key, poll DataMall, or keep a database. Every bus here is
 * invented, which the page states plainly and repeatedly.
 */
window.BusAPI = (function () {
  "use strict";

  var NETWORK_URL = "./network.json";

  // Mirrors busapp/config.py.
  var SGT_OFFSET_S = 8 * 3600;
  var MIN_FOR_CLAIM = 10;
  var PLANNING_QUANTILE = 0.9;
  var DEFAULT_MARGIN_S = 120;
  var EXIT_BUFFER_MIN = 5;
  var MIN_HEADWAY_S = 30;
  var MAX_HEADWAY_S = 5400;
  var BUNCH_WINDOW_S = 180;
  var HISTORY_DAYS = 21;

  // Mirrors SimulatedClient.CHARACTER: base headway minutes, unreliability.
  var CHARACTER = {
    "13": [9, 0.35], "61": [11, 0.55], "67": [12, 0.45], "107": [14, 0.30],
    "107M": [16, 0.30], "133": [10, 0.25], "141": [13, 0.50], "145": [8, 0.60],
    "175": [15, 0.40], "961": [12, 0.35], "961M": [18, 0.25]
  };
  var FIRST_BUS_H = 6, LAST_BUS_H = 24;
  var PEAK = { 7: 1, 8: 1, 9: 1, 17: 1, 18: 1, 19: 1 };
  var LOAD_LABELS = { SEA: "Seats available", SDA: "Standing available", LSD: "Limited standing" };

  var net = null;
  var historyCache = {};
  var totalArrivals = null;

  // --- deterministic randomness -------------------------------------------
  // Not cryptographic and not matching Python's SHA-256; it only has to be
  // stable within a session so ETAs count down instead of jittering.
  function hashString(str) {
    var h = 1779033703 ^ str.length;
    for (var i = 0; i < str.length; i++) {
      h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
      h = (h << 13) | (h >>> 19);
    }
    return function () {
      h = Math.imul(h ^ (h >>> 16), 2246822507);
      h = Math.imul(h ^ (h >>> 13), 3266489909);
      return ((h ^= h >>> 16) >>> 0) / 4294967296;
    };
  }
  function rand() {
    return hashString(Array.prototype.join.call(arguments, "|"))();
  }

  // --- Singapore time ------------------------------------------------------
  function sgt(epoch) {
    var d = new Date((epoch + SGT_OFFSET_S) * 1000);
    return {
      year: d.getUTCFullYear(), month: d.getUTCMonth(), date: d.getUTCDate(),
      hour: d.getUTCHours(), min: d.getUTCMinutes(), sec: d.getUTCSeconds(),
      wday: d.getUTCDay(),
      yday: Math.floor((Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
                        - Date.UTC(d.getUTCFullYear(), 0, 1)) / 86400000)
    };
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hhmm(epoch) { var t = sgt(epoch); return pad(t.hour) + ":" + pad(t.min); }
  function bucketOf(epoch) {
    var t = sgt(epoch);
    return { daytype: (t.wday === 0 || t.wday === 6) ? "weekend" : "weekday", hour: t.hour };
  }

  // --- the simulated timetable --------------------------------------------
  function dayStart(epoch) {
    var t = sgt(epoch);
    return Math.floor(epoch - (t.hour * 3600 + t.min * 60 + t.sec) + FIRST_BUS_H * 3600);
  }

  function actualArrivals(stopCode, service, epoch) {
    var ch = CHARACTER[service] || [12, 0.4];
    var baseMin = ch[0], chaos = ch[1];
    var t = sgt(epoch);
    var start = dayStart(epoch);
    var end = start + (LAST_BUS_H - FIRST_BUS_H) * 3600;
    var out = [], cursor = start, i = 0, gap;
    while (cursor < end && i < 200) {
      out.push(Math.floor(cursor));
      var hour = sgt(cursor).hour;
      var headway = baseMin * (PEAK[hour] ? 0.7 : 1.15);
      var r = rand(t.year, t.yday, stopCode, service, i);
      if (r < chaos * 0.4) gap = headway * 0.15;            // bunched
      else if (r < chaos * 0.7) gap = headway * 2.1;        // the hole after
      else gap = headway * (0.75 + 0.5 * rand("j", t.year, t.yday, stopCode, service, i));
      cursor += Math.max(60, gap * 60);
      i++;
    }
    return out;
  }

  function biasFor(stopCode, service, actual) {
    return (rand("bias", stopCode, service, actual) - 0.5) * 360;
  }

  function liveAt(stopCode, service, now) {
    var upcoming = actualArrivals(stopCode, service, now)
      .filter(function (a) { return a > now - 30; }).slice(0, 3);
    return upcoming.map(function (actual, idx) {
      var remaining = Math.max(0, actual - now);
      var reported = actual + biasFor(stopCode, service, actual) * Math.min(1, remaining / 600);
      var loads = ["SEA", "SDA", "LSD"];
      return {
        slot: idx + 1,
        eta: Math.floor(reported),
        load: loads[Math.floor(rand("load", stopCode, service, actual, Math.floor(now / 60)) * 3)],
        type: rand("type", service, actual) < 0.7 ? "SD" : "DD"
      };
    });
  }

  /* Stand-in for the database: replay the simulated timetable for past days.
   * The real app measures this; here it is manufactured, hence the banner. */
  function history(stopCode, service, now) {
    var key = stopCode + "|" + service;
    if (historyCache[key]) return historyCache[key];
    var rows = [];
    for (var day = 1; day <= HISTORY_DAYS; day++) {
      var when = now - day * 86400;
      actualArrivals(stopCode, service, when).forEach(function (actual) {
        if (actual > now) return;
        var b = bucketOf(actual);
        rows.push({
          arrived_at: actual,
          error_s: Math.round(-biasFor(stopCode, service, actual)),
          daytype: b.daytype, hour: b.hour
        });
      });
    }
    rows.sort(function (a, b) { return a.arrived_at - b.arrived_at; });
    historyCache[key] = rows;
    return rows;
  }

  // --- statistics (port of busapp/stats.py) --------------------------------
  function quantile(values, q) {
    var xs = values.slice().sort(function (a, b) { return a - b; });
    if (!xs.length) return null;
    if (xs.length === 1) return xs[0];
    var pos = (xs.length - 1) * q, lo = Math.floor(pos), hi = Math.min(lo + 1, xs.length - 1);
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo);
  }

  function headways(rows, daytype, hour) {
    var out = [], prev = null;
    rows.forEach(function (r) {
      if (prev !== null) {
        var gap = r.arrived_at - prev;
        if (gap >= MIN_HEADWAY_S && gap <= MAX_HEADWAY_S) {
          if (!daytype) out.push(gap);
          else {
            var b = bucketOf(r.arrived_at);
            if (b.daytype === daytype && (hour == null || b.hour === hour)) out.push(gap);
          }
        }
      }
      prev = r.arrived_at;
    });
    return out;
  }

  /* P(wait > w) = sum(max(0, H - w)) / sum(H), inverted by bisection. A
   * passenger arriving at an arbitrary moment is likelier to land in a long gap,
   * so this exceeds half the mean headway whenever buses bunch. */
  function waitQuantile(gaps, q) {
    gaps = gaps.filter(function (g) { return g > 0; });
    if (!gaps.length) return null;
    var total = gaps.reduce(function (a, b) { return a + b; }, 0);
    var target = 1 - q, lo = 0, hi = Math.max.apply(null, gaps);
    for (var i = 0; i < 60; i++) {
      var mid = (lo + hi) / 2;
      var surv = gaps.reduce(function (a, g) { return a + Math.max(0, g - mid); }, 0) / total;
      if (surv > target) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }

  function meanWait(gaps) {
    gaps = gaps.filter(function (g) { return g > 0; });
    if (!gaps.length) return null;
    var sq = gaps.reduce(function (a, g) { return a + g * g; }, 0);
    return sq / (2 * gaps.reduce(function (a, b) { return a + b; }, 0));
  }

  function earlyMargin(rows, q) {
    var earlies = rows.filter(function (r) { return r.error_s != null; })
                      .map(function (r) { return Math.max(0, -r.error_s); });
    return earlies.length ? quantile(earlies, q) : null;
  }

  function confidenceTier(n) {
    return n >= 30 ? "good" : n >= 10 ? "fair" : n >= 1 ? "thin" : "none";
  }

  function summarize(rows, now) {
    var b = bucketOf(now);
    var bases = [
      ["hour", rows.filter(function (r) { return r.daytype === b.daytype && r.hour === b.hour; }),
       b.daytype + " " + pad(b.hour) + ":00-" + pad(b.hour) + ":59"],
      ["daytype", rows.filter(function (r) { return r.daytype === b.daytype; }),
       "any " + b.daytype + " hour"],
      ["all", rows, "all logged times"]
    ];
    var chosen = bases[bases.length - 1];
    for (var i = 0; i < bases.length; i++) {
      if (bases[i][1].length >= MIN_FOR_CLAIM) { chosen = bases[i]; break; }
    }
    var basis = chosen[0], sample = chosen[1], label = chosen[2];
    var gaps = headways(rows, basis === "all" ? null : b.daytype, basis === "hour" ? b.hour : null);
    var margin = earlyMargin(sample, PLANNING_QUANTILE);
    var claimable = sample.length >= MIN_FOR_CLAIM;
    var wt = meanWait(gaps), wp = waitQuantile(gaps, PLANNING_QUANTILE), hm = quantile(gaps, 0.5);
    return {
      n: sample.length, n_total: rows.length, basis: basis, basis_label: label,
      confidence: confidenceTier(sample.length), claimable: claimable,
      quantile: PLANNING_QUANTILE,
      margin_s: (claimable && margin != null) ? Math.round(margin) : null,
      margin_is_default: !(claimable && margin != null),
      effective_margin_s: (claimable && margin != null) ? Math.round(margin) : DEFAULT_MARGIN_S,
      headway_n: gaps.length,
      headway_median_s: hm != null ? Math.round(hm) : null,
      wait_typical_s: wt != null ? Math.round(wt) : null,
      wait_p90_s: wp != null ? Math.round(wp) : null
    };
  }

  // --- planning (port of busapp/planner.py) --------------------------------
  function optionsFor(destCode) {
    var walkBy = {};
    net.origin.stops.forEach(function (s) { walkBy[s.code] = s; });
    var out = (net.reachable[destCode] || []).map(function (opt) {
      var svc = net.services[opt.key];
      var w = walkBy[svc.board_stop] || { walk_min: 1, walk_m: 0 };
      var stop = net.stops[svc.board_stop];
      return {
        key: opt.key, service: opt.service, direction: svc.direction,
        route_name: svc.name, terminus: svc.terminus,
        board_stop: svc.board_stop, board_name: stop.name, board_road: stop.road,
        walk_min: w.walk_min, walk_m: w.walk_m, hops: opt.hops
      };
    });
    out.sort(function (a, b) { return (a.hops - b.hops) || (a.walk_min - b.walk_min); });
    return out;
  }

  function signalsFor(buses, hist) {
    var out = [];
    if (buses.length >= 2) {
      var gap = buses[1].eta - buses[0].eta;
      if (gap <= BUNCH_WINDOW_S) {
        out.push({ kind: "bunched", text: "Two buses " + Math.max(1, Math.round(gap / 60))
          + " min apart, then likely a long gap. Catch this pair or expect a wait." });
      }
      if (hist.headway_median_s && gap > hist.headway_median_s * 2) {
        out.push({ kind: "gap", text: "Nothing for " + Math.round(gap / 60)
          + " min after this one, against a usual " + Math.round(hist.headway_median_s / 60)
          + " min gap." });
      }
    }
    if (buses.length === 1) {
      out.push({ kind: "only-one", text: "Only one bus is being tracked right now." });
    }
    return out;
  }

  function planOption(option, now) {
    var hist = summarize(history(option.board_stop, option.service, now), now);
    var buses = liveAt(option.board_stop, option.service, now)
                  .sort(function (a, b) { return a.eta - b.eta; });
    var walkS = option.walk_min * 60, exitS = EXIT_BUFFER_MIN * 60;
    var marginS = hist.effective_margin_s;

    var target = null;
    for (var i = 0; i < buses.length; i++) {
      var leaveAt = buses[i].eta - exitS - walkS - marginS;
      if (leaveAt >= now - 60) {
        target = {
          eta_epoch: buses[i].eta, eta_hhmm: hhmm(buses[i].eta),
          in_min: Math.round((buses[i].eta - now) / 60),
          leave_epoch: Math.floor(leaveAt), leave_hhmm: hhmm(leaveAt),
          leave_in_min: Math.round((leaveAt - now) / 60),
          walk_min: option.walk_min, exit_min: EXIT_BUFFER_MIN, exit_s: exitS,
          margin_min: Math.round(marginS / 60), margin_s: marginS,
          margin_is_default: hist.margin_is_default
        };
        break;
      }
    }

    var view = {};
    Object.keys(option).forEach(function (k) { view[k] = option[k]; });
    view.buses = buses.map(function (b) {
      return {
        eta_epoch: b.eta, eta_hhmm: hhmm(b.eta), in_min: Math.round((b.eta - now) / 60),
        load: b.load, load_label: LOAD_LABELS[b.load] || "",
        type: b.type === "DD" ? "Double deck" : "Single deck"
      };
    });
    view.target = target;
    view.too_late_for = (!target && buses.length)
      ? { eta_hhmm: hhmm(buses[0].eta), in_min: Math.round((buses[0].eta - now) / 60) } : null;
    view.history = hist;
    view.signals = signalsFor(buses, hist);
    return view;
  }

  // --- loading -------------------------------------------------------------
  function load() {
    if (net) return Promise.resolve(net);
    return fetch(NETWORK_URL).then(function (r) {
      if (!r.ok) throw new Error("network.json HTTP " + r.status);
      return r.json();
    }).then(function (data) { net = data; return net; });
  }

  function countArrivals(now) {
    if (totalArrivals != null) return totalArrivals;
    var seen = {}, total = 0;
    Object.keys(net.services).forEach(function (k) {
      var svc = net.services[k], key = svc.board_stop + "|" + svc.service;
      if (seen[key]) return;
      seen[key] = 1;
      total += history(svc.board_stop, svc.service, now).length;
    });
    totalArrivals = total;
    return total;
  }

  // --- the BusAPI surface --------------------------------------------------
  return {
    startupHint: "Reload the page.",

    config: function () {
      return load().then(function () {
        var now = Date.now() / 1000;
        var nums = {};
        Object.keys(net.services).forEach(function (k) { nums[net.services[k].service] = 1; });
        var services = Object.keys(nums).sort(function (a, b) {
          return a.length - b.length || a.localeCompare(b);
        });
        return {
          version: net.version || "0.1.0",
          mode: "simulated",
          demo_history: true,
          static_page: true,
          now: Math.floor(now), now_hhmm: hhmm(now),
          origin: {
            name: net.origin.name, address: net.origin.address,
            stops: net.origin.stops
          },
          services: services,
          suggestions: net.suggestions || [],
          poll_stops: net.poll_stops,
          direct_stops: Object.keys(net.reachable).length,
          total_stops: Object.keys(net.stops).length,
          network_generated_at: net.generated_at,
          coverage: {
            arrivals: countArrivals(now), observations: 0,
            last_poll: Math.floor(now), last_poll_ok: true, last_poll_error: null
          },
          min_for_claim: MIN_FOR_CLAIM,
          planning_quantile: PLANNING_QUANTILE,
          default_margin_min: Math.round(DEFAULT_MARGIN_S / 60),
          exit_buffer_min: EXIT_BUFFER_MIN
        };
      });
    },

    search: function (query) {
      return load().then(function () {
        var q = (query || "").trim().toLowerCase();
        if (q.length < 2) return { query: query, results: [] };
        var scored = [];
        Object.keys(net.stops).forEach(function (code) {
          var s = net.stops[code];
          var name = s.name.toLowerCase(), road = (s.road || "").toLowerCase(), score;
          if (q === code.toLowerCase()) score = 0;
          else if (name.indexOf(q) === 0) score = 1;
          else if (name.indexOf(q) >= 0) score = 2;
          else if (road.indexOf(q) === 0) score = 3;
          else if (road.indexOf(q) >= 0) score = 4;
          else if (code.indexOf(q) === 0) score = 5;
          else return;
          var direct = !!net.reachable[code];
          scored.push([direct ? 0 : 1, score, s.name.length, code, s, direct]);
        });
        scored.sort(function (a, b) {
          return a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || (a[3] < b[3] ? -1 : 1);
        });
        return {
          query: query,
          results: scored.slice(0, 12).map(function (t) {
            var code = t[3], s = t[4], opts = net.reachable[code] || [], svcs = {};
            opts.forEach(function (o) { svcs[o.service] = 1; });
            return {
              code: code, name: s.name, road: s.road || "", direct: t[5],
              services: Object.keys(svcs).sort(function (a, b) {
                return a.length - b.length || a.localeCompare(b);
              }),
              hops: opts.length ? opts[0].hops : null
            };
          })
        };
      });
    },

    plan: function (destCode) {
      return load().then(function () {
        var now = Date.now() / 1000;
        var dest = net.stops[destCode];
        if (!dest) return { error: "unknown_stop", code: destCode };
        var options = optionsFor(destCode);
        var result = {
          now: Math.floor(now), now_hhmm: hhmm(now),
          destination: {
            code: destCode, name: dest.name, road: dest.road || "",
            busrouter_url: "https://busrouter.sg/#/stops/" + destCode
          },
          reachable: options.length > 0, options: [], best_key: null
        };
        if (!options.length) return result;
        var planned = options.map(function (o) { return planOption(o, now); });
        planned.sort(function (a, b) {
          if (a.target && b.target) return a.target.eta_epoch - b.target.eta_epoch;
          if (a.target) return -1;
          if (b.target) return 1;
          return a.hops - b.hops;
        });
        result.options = planned;
        result.best_key = planned[0].target ? planned[0].key : null;
        return result;
      });
    }
  };
})();
