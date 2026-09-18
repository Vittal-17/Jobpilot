## 2023-10-25 - Content-Security-Policy blocking FastAPI Docs
**Vulnerability:** Missing Content-Security-Policy headers in the API layer.
**Learning:** Adding a strict `Content-Security-Policy: default-src 'self'` in a FastAPI backend breaks the interactive API documentation (`/docs` for Swagger UI and `/redoc`) because they load scripts, styles, and images from external CDNs (e.g., `cdn.jsdelivr.net`) and use inline scripts.
**Prevention:** When implementing a CSP middleware in FastAPI, ensure that directives like `script-src`, `style-src`, and `img-src` explicitly allow the required CDN domains and `unsafe-inline` to maintain functionality of developer tools, while still restricting the core application assets.
