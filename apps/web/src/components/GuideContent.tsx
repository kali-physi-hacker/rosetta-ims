import { C } from '@/lib/tokens'
import { useEffect, useState } from 'react'
import { authHeaders } from '@/lib/auth'
import { API_BASE } from '@/lib/config'

interface Sum { customers?: number; filters?: { cust?: Record<string, number> } }

const card: React.CSSProperties = { background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px' }
const h2: React.CSSProperties = { fontSize: '15px', fontWeight: 800, color: C.ink, margin: '0 0 4px' }
const sub: React.CSSProperties = { fontSize: '12px', color: C.muted, margin: '0 0 14px' }

function Chip({ children, c = C.indigo, bg = C.primaryBg }: { children: React.ReactNode; c?: string; bg?: string }) {
  return <span style={{ fontSize: '11px', fontWeight: 600, color: c, background: bg, border: `1px solid ${c}33`, borderRadius: '12px', padding: '2px 9px', whiteSpace: 'nowrap' }}>{children}</span>
}

function SourceCard({ icon, name, what, color, n }: { icon: string; name: string; what: string; color: string; n?: number }) {
  return (
    <div style={{ flex: '1 1 180px', border: `2px solid ${color}`, background: `${color}0D`, borderRadius: '10px', padding: '12px 14px' }}>
      <div style={{ fontSize: '13px', fontWeight: 800, color }}>{icon} {name}</div>
      {n != null && <div style={{ fontSize: '20px', fontWeight: 800, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{n.toLocaleString()}</div>}
      <div style={{ fontSize: '11px', color: C.muted, marginTop: '2px' }}>{what}</div>
    </div>
  )
}

/** The "How to use" guide — shared by the inline Clientbase tab and the /clients/guide route.
 *  Pass inline to drop the standalone "Open the Clientbase" footer (redundant when shown as a tab). */
export function GuideContent({ inline = false }: { inline?: boolean }) {
  const [s, setS] = useState<Sum | null>(null)
  useEffect(() => {
    fetch(`${API_BASE}/clients/summary`, { headers: authHeaders() }).then(r => (r.ok ? r.json() : null)).then(setS).catch(() => {})
  }, [])
  const cu = s?.filters?.cust || {}

  return (
    <div style={{ maxWidth: '1100px' }}>
      <h1 style={{ fontSize: '22px', fontWeight: 800, color: C.ink, margin: '0 0 2px' }}>Clientbase — your campaign cockpit 🐾</h1>
      <p style={{ fontSize: '13px', color: C.muted, margin: '0 0 20px', maxWidth: '820px' }}>
        Built for marketing. It joins <b>every purchase</b> across the clinic (Dr Hugh + Ohana) and the website
        into one view, then tells you <b>who to target, with which product, and why</b> — and lets you export the
        audience straight to <b>Meta / Klaviyo / WhatsApp</b>. If you read one thing, start with <b>Marketing Initiatives</b>.
      </p>

      {/* ─── THE 4 TABS ─── */}
      <div style={{ ...card, marginBottom: '18px' }}>
        <h2 style={h2}>The four tabs</h2>
        <p style={sub}>Switch between them at the top of the Clientbase.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '10px' }}>
          {[
            ['📣 Marketing Initiatives', C.indigo, 'START HERE. Ranked campaign opportunities built from real demand — exactly what to run next, to whom, with which product.'],
            ['👥 Client Database', '#0D9488', 'The full, filterable customer list. Build or refine any audience by hand and export it. The default view.'],
            ['📊 Demand Breakdown', C.green, 'Every product ever transacted — by clients, revenue and clinic-vs-online split. The product intelligence behind the initiatives.'],
            ['📈 CRM Performance', '#7C3AED', 'Which lists acquire best and which flows convert best — members → purchasers → conversion → revenue, per list & flow.'],
          ].map(([t, c, d]) => (
            <div key={t} style={{ border: `1px solid ${c}33`, background: `${c}0A`, borderRadius: '8px', padding: '11px 13px' }}>
              <div style={{ fontSize: '13px', fontWeight: 800, color: c }}>{t}</div>
              <div style={{ fontSize: '11px', color: C.sub, marginTop: '3px' }}>{d}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ─── START HERE: MARKETING INITIATIVES ─── */}
      <div style={{ ...card, marginBottom: '18px', borderColor: C.indigoLine, background: '#F5F7FF' }}>
        <h2 style={h2}>📣 Start here: Marketing Initiatives</h2>
        <p style={sub}>The hard question — “who do I target, and with what?” — is answered for you, ranked by opportunity. You don’t need to understand the data plumbing to use this.</p>
        <div style={{ background: '#FFF7ED', border: '1px solid #FDBA74', borderRadius: '8px', padding: '10px 13px', marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: 800, color: '#9A3412' }}>The big idea: don’t judge a list by cost-per-lead alone.</div>
          <div style={{ fontSize: '11px', color: '#7C2D12', marginTop: '3px', lineHeight: 1.5 }}>
            The clinic — especially <b>Dr Hugh’s legacy base</b> — holds thousands of clients who buy <b>Medicines,
            Preventatives and Prescription Diets in person</b> and have <b>never bought online</b>. That clinic→online
            gap is the biggest prize we have, and it’s invisible if you only look at ad metrics. The initiatives rank
            every demand bucket by exactly that gap.
          </div>
        </div>
        <div style={{ fontSize: '12px', fontWeight: 700, color: '#334155', marginBottom: '6px' }}>How to read an initiative card</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <Chip c={C.ink} bg={C.monoBg}>① the target pool (buy in-clinic, not online)</Chip>
          <Chip c={C.green} bg={C.greenBg}>② products to feature</Chip>
          <Chip c={C.amberInk} bg={C.warnBg}>③ reach by channel (✉/💬/📣)</Chip>
          <Chip c={C.ok} bg="#F0FDF4">④ the suggested play</Chip>
          <Chip c={C.indigo} bg={C.primaryBg}>⑤ Build this audience →</Chip>
        </div>
        <div style={{ fontSize: '12px', color: C.sub, lineHeight: 1.6 }}>
          <b>The proven angle:</b> Phase 1 showed “<b>Savings — HK$100 off</b>” wins. Now pair it with the specific
          product the customer already buys — e.g. <i>“Your Revolution — now HK$100 off online.”</i><br />
          <b>The workflow:</b> pick an initiative → <b>Build this audience</b> (drops you into the Client Database, pre-filtered)
          → <b>Export</b> the list → run it in Meta / Klaviyo / WhatsApp → track results in your Marketing SSOT.
        </div>
      </div>

      {/* ─── DATA SOURCES ─── */}
      <div style={{ ...card, marginBottom: '18px' }}>
        <h2 style={h2}>Where the data comes from</h2>
        <p style={sub}>Three customer “books” are merged into one record (matched by email & phone), then enriched with marketing + service signals.</p>

        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <SourceCard icon="🩺" name="Dr Hugh (CHS)" color="#6B21A8" n={cu.chs} what="Legacy clinic export — the pre-acquisition patient history (dispensed meds, diagnoses)." />
          <SourceCard icon="🏥" name="Ohana (DaySmart)" color="#0D9488" n={cu.ohana} what="The live clinic POS — visits + invoiced purchases since takeover (24 Jun 2025)." />
          <SourceCard icon="🛒" name="PetProject (Shopify)" color={C.green} n={cu.online} what="The website — lifetime online orders, LTV, products & SKUs back to 2019." />
        </div>

        <div style={{ textAlign: 'center', color: C.knobOff, fontSize: '20px', margin: '6px 0' }}>↓ matched by email / phone ↓</div>

        <div style={{ background: C.primaryBg, border: '2px solid #6366F1', borderRadius: '10px', padding: '10px 14px', textAlign: 'center', marginBottom: '12px' }}>
          <span style={{ fontSize: '13px', fontWeight: 800, color: '#3730A3' }}>= ONE unified customer record</span>
          {s?.customers != null && <span style={{ fontSize: '13px', color: C.muted }}> · {s.customers.toLocaleString()} active customers (newsletter-only leads hidden by default)</span>}
        </div>

        <div style={{ fontSize: '11px', fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '6px' }}>+ enriched with</div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <Chip c={C.amberInk} bg={C.warnBg}>✉ Klaviyo — email lists, consent, last engagement</Chip>
          <Chip c={C.green} bg={C.greenBg}>💬 ChatArchitect — WhatsApp opt-in list</Chip>
          <Chip c="#DB2777" bg="#FCE7F3">🎧 Slack (WhatsApp mirror) — who’s contacting CS + sentiment</Chip>
          <Chip c={C.bad} bg={C.redBg}>📦 Shopify — unfulfilled orders</Chip>
        </div>
      </div>

      {/* ─── FILTER FAMILIES ─── */}
      <div style={{ ...card, marginBottom: '18px' }}>
        <h2 style={h2}>Build your own audience — the six filters (Client Database)</h2>
        <p style={sub}>When you want to go beyond the ready-made initiatives, the Client Database lets you build any audience by hand. Pick chips within each row, then stack rows. <b>Within Customers / Demand it’s AND</b> (must match all); <b>within Consents / Operations / CRM it’s OR</b> (any). Across rows it’s always AND. The live count + reachability update as you go; then <b>Export</b>.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(310px, 1fr))', gap: '10px' }}>
          {[
            { t: 'Customers', d: 'Which “book” they belong to — Dr Hugh, Ohana, Website (+ New-to-Ohana, One-time, Autoship, Rx clients, PSG, Prospects). Pick 2+ to find the overlap.', e: 'Dr Hugh AND Ohana = returned patients' },
            { t: 'Demand Record', d: 'What they’ve actually bought — by category (Meds, Preventatives, Rx Diets…), specific product (search), or Shopify collection. Plus “but NOT” to exclude.', e: 'Bought NexGard but NOT Heartgard' },
            { t: 'Consents', d: 'How you’re allowed to reach them — No contact / Contact-no-consent (ads only) / Email-able / WhatsApp-able.', e: 'WhatsApp-able → blast list' },
            { t: 'Operations', d: 'Service state — Unfulfilled order, Reached CS, and sentiment (Happy / Fine / Poor with the actual complaint line).', e: 'Unfulfilled + Poor = urgent' },
            { t: 'CRM Marketing', d: 'Which Klaviyo list / flow / promo they’re in — so you can target a list, or exclude people you just emailed.', e: 'In “GIFT100 claimed”' },
            { t: 'Dates / Rx', d: 'Purchased between [X → Y] (e.g. PSG 2020–21 era), No purchase since (lapsed), Pre-acquisition, Bought Rx.', e: 'Purchased 2020 → 2021' },
          ].map(f => (
            <div key={f.t} style={{ border: '1px solid #E2E8F0', borderRadius: '8px', padding: '10px 12px' }}>
              <div style={{ fontSize: '13px', fontWeight: 800, color: C.indigo }}>{f.t}</div>
              <div style={{ fontSize: '11px', color: C.sub, margin: '3px 0 6px' }}>{f.d}</div>
              <Chip>{f.e}</Chip>
            </div>
          ))}
        </div>
      </div>

      {/* ─── RECIPES ─── */}
      <div style={{ ...card }}>
        <h2 style={h2}>A few ways to use it</h2>
        <p style={sub}>Build the combo, read the count, hit <b>Export cohort</b> (or a per-channel list), and run the campaign.</p>
        {[
          { goal: '🔁 Win back lapsed clinic patients', why: 'Patients who bought Rx at Dr Hugh but have gone quiet — push them to re-order online.',
            chips: [['Customers: Dr Hugh', '#6B21A8', '#F3E8FF'], ['Demand: Rx Diets + Meds', C.indigo, C.primaryBg], ['No purchase since 2025-06', C.amber, C.warnBg]], out: 'Export → Meta + Klaviyo reactivation' },
          { goal: '🎯 Retarget the PSG 2020–21 Rx audience', why: 'In 2020–21 PetProject sold prescription meds in partnership with another clinic — those orders carry “PSG” in the Shopify SKU. A proven, high-value Rx buyer base to win back and to seed Meta lookalikes.',
            chips: [['Customers: PSG 2020-21', '#7C3AED', '#F3E8FF'], ['Demand: Meds', C.indigo, C.primaryBg]], out: 'Export → Meta custom + lookalike' },
          { goal: '🛒 Cross-sell a clinic product online', why: 'They buy online AND got Apoquel at the clinic — nudge them to buy it from us too.',
            chips: [['Demand: Apoquel', C.indigo, C.primaryBg], ['Customers: Website', C.green, C.greenBg]], out: 'Export → Meta “buy Apoquel online”' },
          { goal: '💬 WhatsApp the customers who are waiting', why: 'Paid but unfulfilled, and opted in to WhatsApp — send a proactive “sorry, running late”.',
            chips: [['Operations: Unfulfilled', C.bad, C.redBg], ['Consents: WhatsApp-able', C.green, C.greenBg]], out: 'Export → ChatArchitect blast' },
          { goal: '🚫 Don’t double-message', why: 'About to send an email? Exclude anyone who just got the abandoned-cart flow so it doesn’t read as spam.',
            chips: [['CRM Marketing: Abandoned cart', '#0E7490', '#ECFEFF'], ['(use as an exclusion)', C.muted, C.monoBg]], out: 'Filter / exclude before sending' },
        ].map((r, i) => (
          <div key={i} style={{ display: 'flex', gap: '14px', alignItems: 'flex-start', padding: '11px 0', borderTop: i ? '1px solid #F1F5F9' : 'none' }}>
            <div style={{ flex: '0 0 230px' }}>
              <div style={{ fontSize: '13px', fontWeight: 700, color: C.ink }}>{r.goal}</div>
              <div style={{ fontSize: '11px', color: C.muted, marginTop: '2px' }}>{r.why}</div>
            </div>
            <div style={{ flex: 1, display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
              {r.chips.map(([t, c, bg], j) => (
                <span key={j} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                  {j > 0 && <span style={{ color: C.knobOff, fontSize: '11px' }}>+</span>}
                  <Chip c={c} bg={bg}>{t}</Chip>
                </span>
              ))}
              <span style={{ color: C.knobOff, fontSize: '12px' }}>→</span>
              <span style={{ fontSize: '11px', fontWeight: 700, color: C.ok }}>{r.out}</span>
            </div>
          </div>
        ))}
      </div>

      <div style={{ ...card, marginTop: '18px' }}>
        <h2 style={h2}>Naming convention — lists &amp; flows</h2>
        <p style={sub}>So the CRM is legible and a list links to the flow it feeds. Format: <b>[TYPE] - [CHANNEL] - [CAMPAIGN]</b>.</p>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '10px' }}>
          <Chip c="#0E7490" bg="#ECFEFF"><b>LIST</b> = how we onboard / capture consent (top of funnel)</Chip>
          <Chip c="#7C3AED" bg="#F3E8FF"><b>FLOW</b> = how we reach those already onboarded</Chip>
        </div>
        <div style={{ fontSize: '12px', color: C.sub, lineHeight: 1.6 }}>
          <b>LIST</b> channel = WA · SMS · SITE · META · QUIZ · B2B · DHVH · ALL. <b>FLOW</b> purpose = WELCOME · ABANDON · WINBACK · CROSS/UPSELL · AUTOSHIP · RESTOCK · PREEMPT · POSTPURCHASE · INTERNAL · OPS.<br />
          <b>The key:</b> a list and the flow that nurtures it share their tokens, so the link is obvious —
          <Chip c="#0E7490" bg="#ECFEFF">LIST - WA - DHVH</Chip> feeds <Chip c="#7C3AED" bg="#F3E8FF">FLOW - WELCOME - GIFT100 - WA</Chip>;
          <Chip c="#0E7490" bg="#ECFEFF">LIST - SITE - GIFT100</Chip> feeds <Chip c="#7C3AED" bg="#F3E8FF">FLOW - WELCOME - GIFT100 - SITE</Chip>.<br />
          <b>Exceptions:</b> <Chip c={C.muted} bg={C.monoBg}>LIST - ALL - MASTER</Chip> (the unconsented catch-all);
          <Chip c="#7C3AED" bg="#F3E8FF">FLOW - AUTOSHIP - E1…E7</Chip> (subscribers consent by buying, no list needed).
          <span style={{ color: C.faint }}> Convention set in Klaviyo; the tool mirrors it. Raw Klaviyo IDs that can’t be renamed are shown as-is.</span>
        </div>
      </div>

      <div style={{ ...card, marginTop: '18px' }}>
        <h2 style={h2}>Glossary</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '8px' }}>
          {[
            ['🧾 PSG 2020–21', 'PetProject’s 2020–21 prescription era — products sold in partnership with another clinic, flagged by “PSG” in the Shopify SKU. The original high-value Rx buyer base; ideal to win back and to build Meta lookalikes from.'],
            ['📅 Pre-acquisition', 'Their first-ever purchase predates the Dr Hugh takeover (24 Jun 2025) — i.e. they were customers before we took over.'],
            ['📋 Prospect', 'On a mailing/CRM list but has never bought or visited — a lead to convert (not yet a customer).'],
            ['💬 WhatsApp-able vs Reached CS', 'WhatsApp-able = consented/opted-in to WhatsApp (you can blast them). “Reached CS” (Operations) = they’re actively messaging us now — a service signal, not a consent.'],
            ['⏳ Awaiting Rosetta IMS', 'Some figures show a ⏳ badge — clinic $ revenue, product roll-ups and profit margin. The customer counts are real; these dollar/product figures are indicative until Rosetta IMS + OCR unify product SKUs and costs. Treat them as directional for now.'],
            ['🔗 First touch', 'How a customer first arrived online — Campaign/partner (UTM tag you or a partner put on the link), Landing page (the specific page they hit, e.g. a collection), or Referral (an external site that linked to us). Marketing-tagged only — search-engine/direct noise is excluded. NOTE: recent online journeys only (Shopify retains it for a window); clinic-only customers have none, so use it as a supplementary signal, not a base-wide segment.'],
          ].map(([t, d]) => (
            <div key={t} style={{ border: '1px solid #E2E8F0', borderRadius: '8px', padding: '8px 11px' }}>
              <div style={{ fontSize: '12px', fontWeight: 700, color: C.ink }}>{t}</div>
              <div style={{ fontSize: '11px', color: C.muted, marginTop: '2px' }}>{d}</div>
            </div>
          ))}
        </div>
      </div>

      {!inline && <div style={{ textAlign: 'center', margin: '22px 0 6px' }}>
        <a href="/clients" style={{ fontSize: '14px', fontWeight: 700, color: 'white', background: C.indigo, borderRadius: '8px', padding: '10px 22px', textDecoration: 'none' }}>Open the Clientbase →</a>
      </div>}
    </div>
  )
}
