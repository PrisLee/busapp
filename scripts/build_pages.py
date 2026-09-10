"""Build docs/ -- the GitHub Pages demo of the app.

GitHub Pages serves static files only: no Python, no database, no place to keep
an API key. So the published page runs the simulator, the statistics and the
planner in the browser (pages/static-api.js) against a trimmed copy of the route
data. The interface is byte-for-byte the same one the real app serves.

    python3 scripts/build_pages.py

Nothing here fabricates a claim the page does not also disclose: `static_page`
is set in its config, which makes the UI say the data is invented.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from busapp import __version__, config  # noqa: E402
from busapp.network import Network  # noqa: E402

HERE = config.HERE
DOCS = os.path.join(HERE, "docs")
REPO_URL = "https://github.com/PrisLee/busapp"


def trim_network(net: Network) -> dict:
    """Drop everything the browser does not read. Halves the download."""
    return {
        "version": __version__,
        "generated_at": net.generated_at,
        "origin": {
            "name": net.origin["name"],
            "address": net.origin["address"],
            "stops": [
                {k: s[k] for k in ("code", "name", "road", "walk_m", "walk_min", "services")}
                for s in net.origin["stops"]
            ],
        },
        "poll_stops": net.poll_stops,
        # `downstream` is only needed to compute suggestions, done here instead.
        "services": {
            k: {"key": v["key"], "service": v["service"], "direction": v["direction"],
                "name": v["name"], "board_stop": v["board_stop"], "terminus": v["terminus"]}
            for k, v in net.services.items()
        },
        # `board_stop` is reachable via services[key], so it is dropped here.
        "reachable": {
            dest: [{"key": o["key"], "service": o["service"], "hops": o["hops"]} for o in opts]
            for dest, opts in net.reachable.items()
        },
        "stops": {code: {"name": s["name"], "road": s["road"]} for code, s in net.stops.items()},
        "suggestions": net.suggestions(),
    }


def build_index() -> str:
    """The app's own index.html, repointed at the static backend."""
    with open(os.path.join(config.PUBLIC_DIR, "index.html")) as f:
        html = f.read()

    # Absolute paths would break under the /busapp/ subpath GitHub Pages serves from.
    html = html.replace('href="/styles.css"', 'href="styles.css"')
    html = html.replace('<script src="/server-api.js"></script>', '<script src="static-api.js"></script>')
    html = html.replace('<script src="/app.js"></script>', '<script src="app.js"></script>')

    # A demo page should say what it is in its own title and description.
    html = html.replace(
        "<title>When should I leave? · CT Hub 2</title>",
        "<title>When should I leave? · CT Hub 2 (demo)</title>")
    html = html.replace(
        'content="When to leave CT Hub 2 to catch your bus, based on how late buses actually run."',
        'content="Demo of a bus buffer clock for CT Hub 2, Singapore. All data on this page is simulated."')

    # Point people at the source and at how to run it for real.
    html = html.replace(
        """    Live arrivals from <a href="https://datamall.lta.gov.sg" target="_blank" rel="noopener">LTA DataMall</a>.
  </p>""",
        """    Live arrivals from <a href="https://datamall.lta.gov.sg" target="_blank" rel="noopener">LTA DataMall</a>.
  </p>
  <p>
    <strong>This is a demo.</strong> Every bus and statistic here is generated in your
    browser, because GitHub Pages cannot run the collector that makes the real numbers.
    <a href="%s" target="_blank" rel="noopener">Source and setup on GitHub</a> &mdash;
    it takes one command and a free API key to run against real buses.
  </p>""" % REPO_URL)
    return html


def main() -> None:
    net = Network.load()
    if os.path.isdir(DOCS):
        shutil.rmtree(DOCS)
    os.makedirs(DOCS)

    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(build_index())
    for name in ("styles.css", "app.js"):
        shutil.copy(os.path.join(config.PUBLIC_DIR, name), os.path.join(DOCS, name))
    shutil.copy(os.path.join(HERE, "pages", "static-api.js"), os.path.join(DOCS, "static-api.js"))

    trimmed = trim_network(net)
    with open(os.path.join(DOCS, "network.json"), "w") as f:
        json.dump(trimmed, f, separators=(",", ":"))

    # Tell GitHub Pages not to run Jekyll over it.
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    print("built docs/ for GitHub Pages")
    total = 0
    for name in sorted(os.listdir(DOCS)):
        size = os.path.getsize(os.path.join(DOCS, name))
        total += size
        print("  %-16s %6.1f KB" % (name, size / 1024))
    print("  %-16s %6.1f KB total" % ("", total / 1024))
    print("\nFull network.json was %.0f KB; trimmed to %.0f KB." % (
        os.path.getsize(config.NETWORK_PATH) / 1024,
        os.path.getsize(os.path.join(DOCS, "network.json")) / 1024))


if __name__ == "__main__":
    main()
