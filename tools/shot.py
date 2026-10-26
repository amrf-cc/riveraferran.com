#!/usr/bin/env python3
"""Screenshot a page with headless Chrome over CDP (no extra dependencies).

    python3 tools/shot.py <url> <out.png> [width] [height] [full]

Kept in tools/ so it survives the scratch cache being pruned.
"""
import base64
import os
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    import websocket
except ImportError:
    sys.exit("pip install websocket-client (system python usually has it)")

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
url = sys.argv[1]
out = Path(sys.argv[2]).expanduser()
w = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
h = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
full = len(sys.argv) > 5 and sys.argv[5] in ("full", "1", "true")

port = 9411
prof = tempfile.mkdtemp(prefix="shot-")
chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=%d" % port,
                           "--remote-allow-origins=*", "--user-data-dir=" + prof,
                           "--window-size=%d,%d" % (w, h), "--hide-scrollbars",
                           "--force-device-scale-factor=1", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    ws_url = None
    for _ in range(80):
        try:
            tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
            ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.25)
    if not ws_url:
        sys.exit("chrome did not start")
    ws = websocket.create_connection(ws_url, timeout=90)
    n = [0]

    def call(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == n[0]:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    call("Page.enable")
    call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    call("Page.navigate", url=url)
    time.sleep(2.5)
    extra = os.environ.get("SHOT_JS")
    if extra:
        call("Runtime.evaluate", expression=extra, awaitPromise=True)
        time.sleep(1.5)
    if full:
        size = call("Runtime.evaluate", expression="JSON.stringify({h:document.documentElement.scrollHeight})",
                    returnByValue=True)["result"]["value"]
        total = json.loads(size)["h"]
        call("Emulation.setDeviceMetricsOverride", width=w, height=min(total, 8000), deviceScaleFactor=1, mobile=False)
        time.sleep(1.0)
    shot = call("Page.captureScreenshot", format="png", captureBeyondViewport=bool(full))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(shot["data"]))
    print("%s %d bytes" % (out, out.stat().st_size))
finally:
    chrome.terminate()
    shutil.rmtree(prof, ignore_errors=True)
