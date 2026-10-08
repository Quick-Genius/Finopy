#!/usr/bin/env python3
"""One-time Slack connect: you click Allow in Slack, this catches the redirect on localhost, exchanges the code, and writes
SLACK_WEBHOOK_URL into sentinel/.env. The webhook URL, bot token and client secret are never printed or logged.

  python3 scripts/slack_connect.py  # prompts for the client ID and secret without echoing the secret
Then open the printed authorize link in a browser that is signed in to your workspace, pick the channel, click Allow.
"""
import http.server, json, os, pathlib, re, secrets, sys, urllib.parse, urllib.request
from getpass import getpass

CID = os.environ.get("SLACK_CLIENT_ID") or input("Slack app client ID: ").strip()
SECRET = os.environ.get("SLACK_CLIENT_SECRET") or getpass("Rotated Slack app client secret (input hidden): ")
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/slack/oauth"
STATE = secrets.token_urlsafe(16)
ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"
result = {}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not self.path.startswith("/slack/oauth"):
            self.send_response(404); self.end_headers(); return
        msg = "Something went wrong. You can close this tab."
        try:
            if q.get("state", [""])[0] != STATE:
                raise ValueError("state mismatch")
            if "error" in q:
                raise ValueError("slack said: " + q["error"][0])
            data = urllib.parse.urlencode({"client_id": CID, "client_secret": SECRET, "code": q["code"][0], "redirect_uri": REDIRECT}).encode()
            r = json.load(urllib.request.urlopen(urllib.request.Request("https://slack.com/api/oauth.v2.access", data=data), timeout=30))
            if not r.get("ok"):
                raise ValueError("oauth.v2.access: " + str(r.get("error")))
            hook = r["incoming_webhook"]["url"]
            text = ENV.read_text() if ENV.exists() else ""
            line = f"SLACK_WEBHOOK_URL={hook}"
            text = re.sub(r"(?m)^SLACK_WEBHOOK_URL=.*$", lambda _: line, text) if re.search(r"(?m)^SLACK_WEBHOOK_URL=", text) else text.rstrip("\n") + "\n" + line + "\n"
            fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(text)
            result.update(ok=True, channel=r["incoming_webhook"].get("channel"), team=r.get("team", {}).get("name"))
            msg = f"Connected to #{result['channel']}. You can close this tab."
        except Exception as e:                       # never echo secrets: only the reason
            result.update(ok=False, error=str(e)[:150])
        self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers(); self.wfile.write(f"<h2>{msg}</h2>".encode())
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *a):
        pass


import threading
url = ("https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode({"client_id": CID, "scope": "incoming-webhook", "redirect_uri": REDIRECT, "state": STATE}))
print("OPEN THIS LINK (signed in to your workspace), choose the channel, click Allow:\n" + url, flush=True)
srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
srv.timeout = 900
threading.Timer(900, srv.shutdown).start()
srv.serve_forever()
print("RESULT:", json.dumps(result) if result else "timed out, nothing connected", flush=True)
sys.exit(0 if result.get("ok") else 1)
