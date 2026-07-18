import type {
  Product, ProductsResponse, SummaryResponse, PricingResponse, CategoryRule, Supplier,
  AccessAcknowledgement, MyAcknowledgementResponse, AcknowledgementCreateResponse,
  TransformationConfig, ConfigVersionInfo, ConfigTable,
} from './types'
import { getToken } from './auth'
import { API_BASE } from './config'

const BASE = API_BASE

function apiHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  }
}

async function apiFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const qs = params ? '?' + new URLSearchParams(params) : ''
  const res = await fetch(`${BASE}${path}${qs}`, { cache: 'no-store', headers: apiHeaders() })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

async function apiPatch<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PATCH',
    headers: apiHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

async function apiPost<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: apiHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

async function apiPut<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: apiHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API ${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function getProducts(params?: Record<string, string>): Promise<ProductsResponse> {
  return apiFetch<ProductsResponse>('/products', params)
}

export async function getProduct(sku: string): Promise<Product> {
  return apiFetch<Product>(`/products/${sku}`)
}

export async function getSummary(): Promise<SummaryResponse> {
  return apiFetch<SummaryResponse>('/products/summary')
}

export async function getPricingMatrix(params?: Record<string, string>): Promise<PricingResponse> {
  return apiFetch<PricingResponse>('/pricing', params)
}

export async function getProductPricing(sku: string): Promise<Product> {
  return apiFetch<Product>(`/pricing/${sku}`)
}

export async function getCategoryRules(): Promise<CategoryRule[]> {
  return apiFetch<CategoryRule[]>('/category-rules')
}

export async function getSuppliers(): Promise<Supplier[]> {
  return apiFetch<Supplier[]>('/suppliers')
}

// ── Config-driven transformation engine (Phase B/B2) ──
export async function getTransformations(): Promise<TransformationConfig[]> {
  return (await apiFetch<{ transformations: TransformationConfig[] }>('/config/transformations')).transformations
}

export async function getConfigVersions(): Promise<ConfigVersionInfo[]> {
  return (await apiFetch<{ versions: ConfigVersionInfo[] }>('/config/versions')).versions
}

export async function editParameter(key: string, value: number, note?: string): Promise<{ before: number | null; after: number; version_id: number }> {
  return apiPut(`/config/transformations/${key}`, { value, note: note ?? null })
}

export async function editTable(key: string, table: ConfigTable, note?: string): Promise<{ before: unknown; after: ConfigTable; version_id: number }> {
  return apiPut(`/config/transformations/${key}`, { table, note: note ?? null })
}

export async function editFormula(key: string, formula: string, note?: string): Promise<{ before: string | null; after: string; version_id: number }> {
  return apiPut(`/config/transformations/${key}`, { formula, note: note ?? null })
}

export async function validateConfigEdit(key: string, body: { value?: number; table?: ConfigTable; formula?: string }): Promise<{ ok: boolean; error?: string }> {
  return apiPost(`/config/validate?key=${encodeURIComponent(key)}`, body)
}

export async function restoreConfigVersion(versionId: number): Promise<{ restored_from: number; new_version_id: number }> {
  return apiPost(`/config/versions/${versionId}/restore`, {})
}

export async function updateChannelPrice(
  sku: string,
  channel: string,
  selling_price: number,
): Promise<Product> {
  return apiPatch<Product>(`/products/${sku}/channels/${channel}/price`, { selling_price })
}

export async function adjustStock(
  sku: string,
  location: 'clinic' | 'warehouse',
  delta: number,
  reason: string,
): Promise<Product> {
  return apiPatch<Product>(`/products/${sku}/stock/adjust`, { location, delta, reason })
}

// ─── Access acknowledgements (NDA click-wrap) ───────────────────────────────
export async function getMyAcknowledgement(): Promise<MyAcknowledgementResponse> {
  return apiFetch<MyAcknowledgementResponse>('/access-acknowledgements/me')
}

export async function createAcknowledgement(
  github_username: string,
  full_name_typed: string,
  email_requestor: string,
): Promise<AcknowledgementCreateResponse> {
  return apiPost('/access-acknowledgements', { github_username, full_name_typed, email_requestor })
}

export async function listAcknowledgements(): Promise<{ acknowledgements: AccessAcknowledgement[] }> {
  return apiFetch('/access-acknowledgements')
}
