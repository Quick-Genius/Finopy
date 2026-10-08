#!/usr/bin/env bash
# TEST ONLY: a local stand-in for a Slack Incoming Webhook. It records every POST body and answers 200 "ok".
#   ./scripts/slack_mock.sh start   -> prints the URL to use:  SLACK_WEBHOOK_URL=http://slack-mock:9000/ docker compose up -d n8n
#   ./scripts/slack_mock.sh log     -> shows the recorded messages
#   ./scripts/slack_mock.sh stop
set -euo pipefail
case "${1:-}" in
  start)
    docker rm -f slack-mock >/dev/null 2>&1 || true
    docker run -d --name slack-mock --network finops-sentinel_default python:3.12-slim python -u -c '
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        open("/tmp/slack.log", "a").write(json.dumps(json.loads(body)) + "\n")
        self.send_response(200); self.send_header("content-type", "text/plain"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 9000), H).serve_forever()' >/dev/null
    echo "stand-in webhook up. Use: SLACK_WEBHOOK_URL=http://slack-mock:9000/" ;;
  log)  docker exec slack-mock cat /tmp/slack.log ;;
  stop) docker rm -f slack-mock ;;
  *) echo "usage: $0 start|log|stop"; exit 1 ;;
esac
