import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './generated'
import { API_BASE } from '@/lib/config'
import { getToken } from '@/lib/auth'

// Attach the JWT (from the ims_token cookie) as a Bearer token on every request.
const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = getToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
}

/** Typed API client — methods and payloads are checked against the backend OpenAPI schema. */
export const api = createClient<paths>({ baseUrl: API_BASE })
api.use(authMiddleware)
