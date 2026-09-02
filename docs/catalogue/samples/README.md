# Supplier catalogue samples

The actual documents each supplier-source contract was built from, so a contract
can be read next to the thing it describes.

| Supplier | Contract | Source |
|---|---|---|
| [Queen's Pharma](queens-pharma/) | `queens_pharma.zoetis_price_list.v1` | 3 photographed forms + a marketing banner |
| [Asia Vet Medical](asia-vet-medical/) | `asia_vet_medical.vetriscience_price_list.v1` | 1 photographed list, plus a consumables list nothing reads yet |
| [United Italian](united-italian/) | `united_italian.gp_price_list.v1` | 1 born-digital PDF, 40 pages |
| [IDEXX](idexx/) | `idexx.order_portal_snapshot.v1` | none — the catalogue is fetched from a portal |

Suppliers whose catalogues are **fetched rather than sent** have no file here:
IDEXX and Royal Canin are both read live and emit a snapshot, and their READMEs
say so where a document would otherwise be.

These are evidence. Do not edit, crop or recompress them — the recorded vision
envelopes in `apps/api/tests/fixtures/catalogue_pipeline/` were read from these
exact bytes, and a re-record against an altered image proves nothing about what
the supplier actually sent.

Nothing in this directory is loaded at runtime. Ingestion reads uploads from
`CATALOGUE_UPLOAD_DIR`; these are here to be looked at by people.
