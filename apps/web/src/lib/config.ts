// Single source of truth for the backend base URL.
//
// Dev  → VITE_API_URL is unset, so requests go to the same-origin `/api/v1`
//        prefix, and Vite proxies `/api/*` to the backend (see vite.config.ts).
// Prod → set VITE_API_URL to the backend origin, e.g. https://178.128.127.5.nip.io.
//        The current API version is appended here.
//
// (The old Next.js app had three inconsistent fallbacks — nip.io, localhost:8001 and
// fly.dev — scattered across files. This is the one place it's defined now.)
const API_VERSION = (import.meta.env.VITE_API_VERSION ?? 'v1').replace(/^\/+|\/+$/g, '')
const API_ORIGIN = (import.meta.env.VITE_API_URL ?? '/api').replace(/\/+$/, '')

export const API_BASE = API_ORIGIN.endsWith(`/${API_VERSION}`)
  ? API_ORIGIN
  : `${API_ORIGIN}/${API_VERSION}`
