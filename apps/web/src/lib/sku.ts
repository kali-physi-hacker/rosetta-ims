/**
 * Encode a sku_code for use in a URL path (the /items/[...sku] detail route and the
 * /products/{sku:path} API). A sku_code can contain '/' — e.g. "ACTH (Cosacthen) Inj 0.25mg/ml" —
 * so slashes are kept as real path segments (the catch-all route / FastAPI :path converter
 * reassemble them) while everything else (spaces, parens, #, …) is percent-encoded per segment.
 */
export function skuToPath(sku: string): string {
  return sku.split('/').map(encodeURIComponent).join('/')
}
