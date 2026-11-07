#!/usr/bin/env bash
# Teste de integração da Edge Function `registrar-visita` no runtime oficial do Deno (Docker).
# O `supabase functions serve` exige o stack local completo; aqui basta o Deno e um REST falso.
# Confere: contagem, bot, DNT/GPC, rate limit, CORS e que o IP não aparece em log nem no que é gravado.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'docker rm -f fn-visita >/dev/null 2>&1 || true; kill "${REST_PID:-0}" 2>/dev/null || true' EXIT

python3 - <<'PY' &
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        corpo = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        open("/tmp/rest_recebido.jsonl", "a").write(json.dumps(json.loads(corpo)) + "\n")
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 54399), H).serve_forever()
PY
REST_PID=$!
rm -f /tmp/rest_recebido.jsonl
docker run -d --name fn-visita --network host -v "$PWD/supabase/functions/registrar-visita:/app:ro" -w /app \
  -e DENO_SERVE_ADDRESS=tcp:127.0.0.1:54398 -e SUPABASE_URL=http://127.0.0.1:54399 \
  -e SUPABASE_SERVICE_ROLE_KEY=chave-falsa -e ORIGENS_PERMITIDAS=https://jkfa.github.io \
  denoland/deno:alpine run --allow-net --allow-env --allow-read index.ts >/dev/null
sleep 4
F=http://127.0.0.1:54398; UA="Mozilla/5.0 (iPhone) Mobile Safari"
c() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
[ "$(c -X POST $F -H "User-Agent: $UA" -H 'X-Forwarded-For: 203.0.113.50' -d '{"pagina":"home"}')" = 204 ]
[ "$(c -X POST $F -H "User-Agent: Googlebot/2.1" -H 'X-Forwarded-For: 198.51.100.1' -d '{"pagina":"home"}')" = 204 ]
[ "$(c -X POST $F -H "User-Agent: $UA" -H 'X-Forwarded-For: 198.51.100.2' -d '{"pagina":"admin"}')" = 400 ]
[ "$(c $F -H "User-Agent: $UA")" = 405 ]
for i in 1 2 3; do c -X POST $F -H "User-Agent: $UA" -H 'X-Forwarded-For: 198.51.100.9' -d '{"pagina":"home"}' >/dev/null; done
[ "$(c -X POST $F -H "User-Agent: $UA" -H 'X-Forwarded-For: 198.51.100.9' -d '{"pagina":"home"}')" = 429 ]
sleep 1
! docker logs fn-visita 2>&1 | grep -qE '203\.0\.113|198\.51\.100' || { echo "IP apareceu no log"; exit 1; }
! grep -qE '203\.0\.113|198\.51\.100' /tmp/rest_recebido.jsonl || { echo "IP apareceu no que foi gravado"; exit 1; }
[ "$(grep -c . /tmp/rest_recebido.jsonl)" -ge 4 ]   # o bot e a página inválida não geram registro
echo "registrar-visita: OK"
