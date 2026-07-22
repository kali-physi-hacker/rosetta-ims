/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend origin. Unset in dev (calls go to the `/api` proxy). */
  readonly VITE_API_URL?: string
  /** Backend API version path segment. Defaults to `v1`. */
  readonly VITE_API_VERSION?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
