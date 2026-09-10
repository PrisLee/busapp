"""HTTP server: a small JSON API plus the static front end. Python stdlib only."""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__, config, planner, stats, store
from .datamall import build_client
from .network import Network

# Live arrivals are cached briefly so that several page loads (or several
# services sharing a stop) do not each cost a DataMall call.
LIVE_TTL_S = 20


class CachingClient:
    """Wraps an arrivals client with a short per-stop cache."""

    def __init__(self, inner, ttl: int = LIVE_TTL_S):
        self.inner = inner
        self.ttl = ttl
        self.name = inner.name
        self.live = inner.live
        self._cache: Dict[str, Tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def fetch(self, stop_code: str) -> dict:
        now = time.time()
        with self._lock:
            hit = self._cache.get(stop_code)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        payload = self.inner.fetch(stop_code)
        with self._lock:
            self._cache[stop_code] = (now, payload)
        return payload


class App:
    """Shared state. One instance per process."""

    def __init__(self):
        self.network = Network.load()
        self.client = CachingClient(build_client(self.network))
        self._local = threading.local()

    @property
    def conn(self):
        # SQLite connections are not shareable across threads.
        if getattr(self._local, "conn", None) is None:
            self._local.conn = store.connect()
        return self._local.conn

    # --- endpoints ---------------------------------------------------------
    def api_config(self) -> dict:
        net, conn = self.network, self.conn
        cov = store.coverage(conn)
        origin = net.origin
        return {
            "version": __version__,
            "mode": "live" if self.client.live else "simulated",
            "demo_history": store.get_meta(conn, "demo_history", "") == "1",
            "now": int(time.time()),
            "now_hhmm": stats.sgt_hhmm(time.time()),
            "origin": {
                "name": origin["name"],
                "address": origin["address"],
                "stops": [
                    {k: s[k] for k in ("code", "name", "road", "walk_m", "walk_min", "services")}
                    for s in origin["stops"]
                ],
            },
            "services": net.service_numbers(),
            "suggestions": net.suggestions(),
            "poll_stops": net.poll_stops,
            "direct_stops": len(net.reachable),
            "total_stops": len(net.stops),
            "network_generated_at": net.generated_at,
            "coverage": cov,
            "min_for_claim": config.MIN_FOR_CLAIM,
            "planning_quantile": config.PLANNING_QUANTILE,
            "default_margin_min": int(round(config.DEFAULT_MARGIN_S / 60)),
        }

    def api_search(self, query: str) -> dict:
        return {"query": query, "results": self.network.search(query)}

    def api_plan(self, dest: str) -> dict:
        return planner.plan(self.conn, self.network, self.client, dest)

    def api_reliability(self) -> dict:
        """Per-service track record at its boarding stop -- the data, laid bare."""
        out = []
        for svc in sorted(self.network.services.values(),
                          key=lambda s: (len(s["service"]), s["service"], s["direction"])):
            rows = store.arrivals_for(self.conn, svc["board_stop"], svc["service"])
            summary = stats.summarize(rows)
            out.append({
                "service": svc["service"],
                "direction": svc["direction"],
                "terminus": svc["terminus"],
                "board_stop": svc["board_stop"],
                "board_name": self.network.stops[svc["board_stop"]]["name"],
                "history": summary,
            })
        return {"services": out, "coverage": store.coverage(self.conn)}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "busapp/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if os.environ.get("BUSAPP_ACCESS_LOG"):
                super().log_message(fmt, *args)

        # --- helpers -------------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _static(self, path: str):
            rel = path.lstrip("/") or "index.html"
            full = os.path.normpath(os.path.join(config.PUBLIC_DIR, rel))
            if not full.startswith(os.path.abspath(config.PUBLIC_DIR)) or not os.path.isfile(full):
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype, cache="no-cache")

        # --- routing -------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            route = url.path.rstrip("/") or "/"
            try:
                if route == "/api/config":
                    self._json(app.api_config())
                elif route == "/api/search":
                    self._json(app.api_search((q.get("q") or [""])[0]))
                elif route == "/api/plan":
                    dest = (q.get("dest") or [""])[0]
                    if not dest:
                        self._json({"error": "missing_dest"}, 400)
                        return
                    result = app.api_plan(dest)
                    self._json(result, 404 if result.get("error") == "unknown_stop" else 200)
                elif route == "/api/reliability":
                    self._json(app.api_reliability())
                elif route == "/api/health":
                    self._json({"ok": True, "mode": "live" if app.client.live else "simulated"})
                elif route.startswith("/api/"):
                    self._json({"error": "unknown_endpoint"}, 404)
                else:
                    self._static(url.path)
            except BrokenPipeError:
                pass
            except Exception as e:
                self._json({"error": "server_error", "detail": repr(e)}, 500)

    return Handler


def serve(host: Optional[str] = None, port: Optional[int] = None,
          with_collector: bool = True) -> None:
    from .collector import Collector

    host = host or config.HOST
    port = port or config.PORT
    app = App()

    if with_collector:
        def run():
            conn = store.connect()
            Collector(conn, app.network, client=app.client.inner, verbose=True).run_forever()

        threading.Thread(target=run, daemon=True, name="collector").start()

    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    mode = "LIVE DataMall" if app.client.live else "SIMULATED data (no DATAMALL_API_KEY)"
    print("busapp %s -- %s" % (__version__, mode))
    print("  origin   : %s" % app.network.origin["name"])
    print("  polling  : %s" % ", ".join(app.network.poll_stops))
    print("  collector: %s" % ("on, every %ds" % config.POLL_INTERVAL_S if with_collector else "off"))
    print("  open     : http://%s:%d" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
