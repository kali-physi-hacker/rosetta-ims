/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend base URL. Unset in dev (calls go to the `/api` proxy). */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
