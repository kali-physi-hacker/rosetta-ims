// Canonical Command-Centre palette — the shared source of truth for colour tokens.
//
// The re-parse screens consume `C`; other screens migrate onto it incrementally
// (see MIGRATION.md — a blind literal→token sweep is unsafe because some screens
// build colours by string-concatenating an alpha suffix, e.g. `${color}33`).
// The CSS-variable mirror for global/`<style>`-block use lives in styles/globals.css.
export const C = {
  panel: '#FFFFFF',
  ink: '#0F172A',
  sub: '#475569',
  faint: '#94A3B8',
  line: '#E2E8F0',
  indigo: '#6366F1',
  indigoBg: '#EEF0FE',
  indigoInk: '#4338CA',
  indigoLine: '#C7D2FE',
  ok: '#15803D',
  okBg: '#ECFDF5',
  okLine: '#A7F3D0',
  bad: '#B91C1C',
  badBg: '#FEF2F2',
  amber: '#B45309',
  amberBg: '#FEF6E7',
  amberLine: '#FCD9A6',
  monoBg: '#F1F5F9',
  knobOff: '#CBD5E1',
} as const
