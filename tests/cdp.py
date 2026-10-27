"""A tiny headless-Chrome driver over the DevTools protocol (needs `websocket-client`, which the system
python has). It exists so the editor's UI can be loaded, clicked and screenshotted by tests and probes.

    with Browser(1440, 900) as b:
        page = b.open("http://127.0.0.1:8820/")
        page.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        page.js("document.querySelector('.eye').click()")
        print(page.errors)           # console errors and uncaught exceptions seen so far
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def available():
    try:
        import websocket  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(CHROME)


class JsError(Exception):
    pass


class Page:
    def __init__(self, ws_url, port=None, target_id=None):
        import websocket
        self.port, self.target_id = port, target_id
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.n = 0
        self.events = []
        self.errors = []
        for domain in ("Page", "Runtime", "Log"):
            self.call(domain + ".enable")

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise JsError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            self._event(msg)

    def _event(self, msg):
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.errors.append("exception: %s" % ((d.get("exception") or {}).get("description") or d.get("text")))
        elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            self.errors.append("console.%s: %s" % (p["type"], " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))))
        elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
            e = p["entry"]
            self.errors.append("log: %s %s" % (e.get("text"), e.get("url", "")))

    def pump(self, seconds=0.3):
        """Let queued events (console messages) arrive."""
        self.ws.settimeout(seconds)
        try:
            while True:
                self._event(json.loads(self.ws.recv()))
        except Exception:
            pass
        finally:
            self.ws.settimeout(60)

    def goto(self, url):
        self.call("Page.navigate", url=url)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise JsError((d.get("exception") or {}).get("description") or d.get("text"))
        return r.get("result", {}).get("value")

    def wait(self, expr, timeout=15.0, what=None):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self.js(expr)
            except JsError as e:
                last = str(e)
            if last is True or (last and not isinstance(last, str)):
                return last
            time.sleep(0.1)
        self.pump()
        raise AssertionError("timed out waiting for %s (last value %r; page errors %r)" % (what or expr, last, self.errors))

    def shot(self, path, full=False):
        data = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=bool(full))["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def close(self):
        """Close the connection and the tab, so the next page starts with no log history."""
        try:
            self.ws.close()
        finally:
            if self.port and self.target_id:
                try:
                    urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (self.port, self.target_id), timeout=5).read()
                except Exception:
                    pass

    def set_size(self, w, h):
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)


class Browser:
    def __init__(self, width=1440, height=900):
        self.size = (width, height)

    def __enter__(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.profile = tempfile.mkdtemp(prefix="cdp-")
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--remote-debugging-port=%d" % self.port, "--remote-allow-origins=*",
             "--user-data-dir=" + self.profile, "--window-size=%d,%d" % self.size, "--hide-scrollbars",
             "--force-device-scale-factor=1", "--no-first-run", "--disable-extensions", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws_url = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % self.port, timeout=2))
                pages = [t for t in tabs if t["type"] == "page"]
                if pages:
                    self.ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.2)
        if not self.ws_url:
            self.__exit__(None, None, None)
            raise RuntimeError("Chrome did not start")
        return self

    def open(self, url):
        """A fresh tab. Reusing one would replay the previous page's console log into this one."""
        req = urllib.request.Request("http://127.0.0.1:%d/json/new?about:blank" % self.port, method="PUT")
        tab = json.load(urllib.request.urlopen(req, timeout=10))
        page = Page(tab["webSocketDebuggerUrl"], self.port, tab["id"])
        page.set_size(*self.size)
        page.goto(url)
        return page

    def __exit__(self, *exc):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)
