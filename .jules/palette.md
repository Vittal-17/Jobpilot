## 2024-03-24 - Accessibility and UX improvements in auth-gateway
**Learning:** Pure HTML/CSS login forms in single-file Python web gateways (like auth-gateway/templates/login.html) often lack basic ARIA roles for screen readers, state synchronization for dynamic password toggles, and robust keyboard navigation focus states.
**Action:** When working on similar auth templates, check for missing `aria-live` on error containers, `aria-pressed` / `aria-label` updates on toggle buttons, and CSS `:focus-visible` to ensure keyboard accessibility.
