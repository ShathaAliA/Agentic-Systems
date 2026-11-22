# Security baseline — HTTP surface (PR-A)

Scope: the web application (React front end and this API) served from **one origin** behind a **TLS-terminating
reverse proxy**. Code: `app/http_security.py`, wired in `app/main.py`. Later packages (AI guardrails, supply-chain
scanning, authentication hardening) extend this document.

## Decisions in force

| Topic | Decision |
| --- | --- |
| Client | Web app, not desktop |
| Hosting | Reverse proxy with HTTPS in front of the API; the proxy is the first line for TLS, request rate limits and body size |
| CORS | Same origin: off by default. Wildcards are refused; origins must be exact and https outside development |
| API documentation (`/docs`, `/redoc`, `/openapi.json`) | Off in production; only development/test, or `ENABLE_API_DOCS=1` |
| Extra throttling of upload/admin routes | Not in the application; use the proxy limits below |

## Controls

| Control | Behaviour | Test |
| --- | --- | --- |
| Security headers on every response (also 4xx/5xx and errors from inner layers) | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, CSP `default-src 'none'`. Values set by inner code are kept | `test_http_security.py` |
| HSTS | Only when the effective scheme is https (directly or via a trusted proxy); never over http | same |
| Host check | Exact hosts only; outside development/test only loopback is accepted until `ALLOWED_HOSTS` is set | same |
| Reverse-proxy trust | `X-Forwarded-For/-Proto` are used only when the direct peer is listed in `TRUSTED_PROXY_IPS`; the client is the first non-proxy address from the right; garbage means ignore | same |
| HTTPS posture | Optional `HTTPS_REQUIRED=1`: http requests get 308 (GET/HEAD) or 400; a loopback peer may reach `/health` | same |
| Body limits | Default 1 MiB; revisions 5 MiB; uploads 21 MiB (20 MiB file + framing). Rejected before parsing and while streaming (413) | same |
| Server header | `--no-server-header` in the container command | `test_container_configuration.py` |

The login limiter keys on the client address, so listing the proxy matters: without `TRUSTED_PROXY_IPS` every
client behind the proxy looks like the proxy and shares one per-IP budget.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALLOWED_HOSTS` | loopback only (plus `testserver` in development/test) | Comma-separated exact host names, optional `*.example.com` |
| `CORS_ALLOWED_ORIGINS` | empty (CORS off) | Exact origins `https://portal.example.com` |
| `HTTPS_REQUIRED` | off | Refuse requests whose effective scheme is http |
| `TRUSTED_PROXY_IPS` | empty (forwarded headers ignored) | Exact proxy addresses or CIDRs |
| `ENABLE_API_DOCS` | by environment | `1` to expose the API documentation outside development |

Invalid values stop start-up (fail closed). Missing values never open anything.

## Reverse-proxy reference (nginx)

The proxy must overwrite, not append, the forwarded headers, so a client cannot inject its own.

```nginx
limit_req_zone $binary_remote_addr zone=upload:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=admin:10m  rate=30r/m;
server_tokens off;

server { listen 80; server_name portal.example.com; return 301 https://$host$request_uri; }

server {
    listen 443 ssl;  # http2 on;
    server_name portal.example.com;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 21m;

    location = /api/documents/upload { limit_req zone=upload burst=3 nodelay; include proxy.conf; }
    location /admin/                 { limit_req zone=admin  burst=20 nodelay; include proxy.conf; }
    location /                       { include proxy.conf; }
}
# proxy.conf
#   proxy_pass http://backend:8000;
#   proxy_set_header Host $host;
#   proxy_set_header X-Forwarded-For $remote_addr;   # overwrite
#   proxy_set_header X-Forwarded-Proto $scheme;
```

Then set `ALLOWED_HOSTS=portal.example.com`, `TRUSTED_PROXY_IPS=<proxy address>`, `HTTPS_REQUIRED=1`.

## Not covered here

Production login (Microsoft Entra), secrets manager, TLS certificates and WAF, encryption at rest, front-end
(XSS) controls, AI guardrails and supply-chain scanning (separate packages).
