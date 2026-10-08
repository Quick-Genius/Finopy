#!/usr/bin/env bash
# TEST ONLY: a local stand-in for the Cohere v2 chat API. Modes: faithful | hallucinate | error.
#   ./scripts/llm_mock.sh start faithful   then:  LLM_PROVIDER=cohere LLM_API_KEY=test LLM_BASE_URL=http://llm-mock:9001 docker compose up -d api
#   ./scripts/llm_mock.sh log | stop
set -euo pipefail
case "${1:-}" in
  start)
    docker rm -f llm-mock >/dev/null 2>&1 || true
    docker run -d --name llm-mock --network finops-sentinel_default -e MODE="${2:-faithful}" python:3.12-slim python -u -c '
import http.server, json, os
MODE = os.environ["MODE"]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        open("/tmp/llm.log", "a").write(raw.replace("\n", " ") + "\n")
        if MODE == "error":
            self.send_response(500); self.end_headers(); return
        p = json.loads(json.loads(raw)["messages"][1]["content"])
        o = p["owner"]["name"] or "not established"
        text = (p["workload"] + " in " + p["tenant"] + " is assessed as " + p["classification"].replace("_", " ") + " and the case is " + p["state"] + ". "
                "The owner is " + o + " with confidence " + str(p["owner"]["confidence"]) + ". "
                "Projected saving is " + str(p["projected_monthly"]) + " " + p["currency"] + " per month; realized savings are not measured.")
        if MODE == "hallucinate":
            text += " Savings could reach 9,999 USD and i-deadbeefdeadbeef is also affected."
        body = json.dumps({"message": {"content": [{"type": "text", "text": text}]}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 9001), H).serve_forever()' >/dev/null
    echo "LLM stand-in up (mode ${2:-faithful}) at http://llm-mock:9001" ;;
  log)  docker exec llm-mock cat /tmp/llm.log ;;
  stop) docker rm -f llm-mock ;;
  *) echo "usage: $0 start [faithful|hallucinate|error] | log | stop"; exit 1 ;;
esac
