import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { components } from './generated'
import { api } from './client'

// The backend's GET /category-rules has no response_model, so its OpenAPI response type
// is `unknown`. This is the row shape the Categories screen actually renders.
export interface CategoryRule {
  category: string
  gp_floor: number
  storage_rule: string
  channel_restriction: string | null
  sku_digit: string | null
}

export type RuleCreate = components['schemas']['RuleCreate']
export type RuleUpdate = components['schemas']['RuleUpdate']

const categoryRulesKey = ['category-rules'] as const

export function useCategoryRules() {
  return useQuery({
    queryKey: categoryRulesKey,
    queryFn: async (): Promise<CategoryRule[]> => {
      const { data, error } = await api.GET('/category-rules')
      if (error) throw new Error('Could not load categories')
      return (data ?? []) as CategoryRule[]
    },
  })
}

export function useCreateCategoryRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: RuleCreate) => {
      const { error } = await api.POST('/category-rules', { body })
      if (error) throw error
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: categoryRulesKey }) },
  })
}

export function useUpdateCategoryRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ category, body }: { category: string; body: RuleUpdate }) => {
      const { error } = await api.PATCH('/category-rules/{category}', { params: { path: { category } }, body })
      if (error) throw error
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: categoryRulesKey }) },
  })
}

export function useDeleteCategoryRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (category: string) => {
      const { error } = await api.DELETE('/category-rules/{category}', { params: { path: { category } } })
      if (error) throw error
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: categoryRulesKey }) },
  })
}

/** Pull a FastAPI `{ detail }` message off a thrown API error, for toasts. */
export function apiErrorDetail(err: unknown, fallback: string): string {
  const detail = (err as { detail?: unknown } | null)?.detail
  return typeof detail === 'string' ? detail : fallback
}
