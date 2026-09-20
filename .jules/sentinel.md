## 2024-05-24 - Configure Content-Security-Policy for FastAPI Interactive Docs
**Vulnerability:** Missing security headers (CSP, X-Frame-Options, X-Content-Type-Options, etc.), exposing the application to various attacks like clickjacking and XSS.
**Learning:** When configuring Content-Security-Policy (CSP) headers in a FastAPI backend, setting a strict `default-src 'self'` policy will break the built-in interactive API documentation (/docs and /redoc). These endpoints rely on external assets from jsdelivr and tiangolo.
**Prevention:** Explicitly allow `script-src`, `style-src` from `cdn.jsdelivr.net` and `img-src` from `fastapi.tiangolo.com` in the CSP header to ensure the Swagger UI functions correctly while maintaining a secure default policy.
