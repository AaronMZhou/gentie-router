"""Headless-Chrome page smoke test.

    python tools/smoke_pages.py URL [URL ...] [--no-server] [--no-seed] [--eval JS ...] [--width W]

URL is a path like /dashboard.html (or a full http URL). Each page must follow the
boot contract in frontend/styles/STYLES.md: <html data-ready="1"> once booted and no
recorded data-errors. By default uvicorn serves the app on :8767 (ROUTE_WORKERS=0,
temp DB, seeded with the sample projects; --no-seed starts it empty). --no-server serves frontend/ with a static http.server on :8768,
so there is no API at all. The window is W x 900 (default 1440, the desktop layout;
use --width 390 for the phone layout).

--eval JS (repeatable) is an expression evaluated in page global scope after
data-ready="1"; it may use `await` or return a Promise. If every one is truthy,
<html data-check="true">, otherwise the page fails. The script is injected by a small
reverse proxy on :8769 that rewrites HTML responses, so pages need no hook for it.
"""
import argparse
import html.parser
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_PORT, STATIC_PORT, PROXY_PORT = 8767, 8768, 8769
CHROME_CANDIDATES = [os.getenv("CHROME", ""), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "google-chrome", "chromium", "chromium-browser"]

EVAL_JS = """(function(){var C=%s,d=document.documentElement;
function go(){if(d.dataset.ready!=="1")return setTimeout(go,50);
Promise.all(C.map(function(c){return (0,eval)("(async()=>("+c+"))()");})).then(function(r){
var bad=[];r.forEach(function(v,i){if(!v)bad.push(i);});d.dataset.check=bad.length?"false:"+bad.join(","):"true";
},function(e){d.dataset.check="error:"+(e&&e.message||e);});}
try{go();}catch(e){d.dataset.check="error:"+e.message;}})();"""


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and (Path(c).exists() or shutil.which(c)):
            return c
    sys.exit("FAIL: Chrome not found (set CHROME=/path/to/chrome)")


def wait_http(url, proc, log, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            sys.exit(f"FAIL: server exited ({proc.returncode}):\n{Path(log).read_text()[-2000:]}")
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # server is answering
        except OSError:
            time.sleep(0.25)
    sys.exit(f"FAIL: server at {url} did not come up in {timeout}s")


def start_proxy(upstream, evals):
    script = ("<script>" + EVAL_JS % json.dumps(evals) + "</script>").encode()

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _forward(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding", "connection")}
            req = urllib.request.Request(upstream + self.path, data=body, headers=hdrs, method=self.command)
            try:
                r = urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as e:
                r = e
            data = r.read()
            if "text/html" in (r.headers.get("Content-Type") or ""):
                i = data.lower().find(b"<head>")
                data = data[:i + 6] + script + data[i + 6:] if i >= 0 else script + data
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _forward

    srv = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class HtmlAttrs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.attrs = None

    def handle_starttag(self, tag, attrs):
        if tag == "html" and self.attrs is None:
            self.attrs = dict(attrs)


def check(chrome, url, profile, want_check, width):
    cmd = [chrome, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
           "--virtual-time-budget=15000", f"--window-size={width},900", "--dump-dom", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "chrome timed out"
    if p.returncode != 0:
        return f"chrome exited {p.returncode}: {p.stderr.strip()[-300:]}"
    parser = HtmlAttrs()
    parser.feed(p.stdout)
    a = parser.attrs
    if a is None:
        return "no <html> in DOM dump"
    if a.get("data-errors"):
        return f"errors: {a['data-errors']}"
    if a.get("data-ready") != "1":
        return "data-ready missing"
    if want_check and a.get("data-check") != "true":
        return f"data-check={a.get('data-check')!r}"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--no-server", action="store_true", help="static files only, no API")
    ap.add_argument("--no-seed", action="store_true", help="start the API with an empty project store")
    ap.add_argument("--eval", action="append", default=[], metavar="JS", help="truthy check, repeatable")
    ap.add_argument("--width", type=int, default=1440, metavar="W", help="window width in px (height 900)")
    args = ap.parse_args()

    chrome = find_chrome()
    tmp = tempfile.mkdtemp(prefix="gentie-smoke-")
    log = os.path.join(tmp, "server.log")
    env = dict(os.environ, ROUTE_WORKERS="0", GENTIE_NO_SEED="1" if args.no_seed else "0", GENTIE_DB=os.path.join(tmp, "projects.db"))
    if args.no_server:
        port = STATIC_PORT
        cmd = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(ROOT / "frontend")]
    else:
        port = API_PORT
        cmd = [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", str(port)]
    base = f"http://127.0.0.1:{port}"
    # a stale server on the port would answer wait_http and get tested instead of this checkout
    for p in (port, PROXY_PORT) if args.eval else (port,):
        try:
            socket.create_connection(("127.0.0.1", p), 0.5).close()
            shutil.rmtree(tmp, ignore_errors=True)
            sys.exit(f"FAIL: port {p} already in use")
        except OSError:
            pass
    with open(log, "w") as lf:
        server = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT)
    proxy = None
    failed = 0
    try:
        wait_http(base + "/", server, log)
        if args.eval:
            proxy = start_proxy(base, args.eval)
            base = f"http://127.0.0.1:{PROXY_PORT}"
        for i, u in enumerate(args.urls):
            url = u if u.startswith("http") else base + "/" + u.lstrip("/")
            reason = check(chrome, url, os.path.join(tmp, f"profile-{i}"), bool(args.eval), args.width)
            if not reason and server.poll() is not None:
                reason = f"server exited ({server.returncode}) during the run"
            print(f"FAIL {u}: {reason}" if reason else f"OK {u}", flush=True)
            failed += bool(reason)
    finally:
        if proxy:
            proxy.shutdown()
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
