// Single source of truth for the backend base URL.
//
// Dev  → VITE_API_URL is unset, so requests go to the same-origin `/api` prefix,
//        which Vite proxies to the backend (see vite.config.ts). No CORS setup needed.
// Prod → set VITE_API_URL to the backend origin, e.g. https://178.128.127.5.nip.io
//
// (The old Next.js app had three inconsistent fallbacks — nip.io, localhost:8001 and
// fly.dev — scattered across files. This is the one place it's defined now.)
export const API_BASE = import.meta.env.VITE_API_URL ?? '/api'
