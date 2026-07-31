// Inventory rev-02 stylesheet — scope switch, attention rail, view tabs,
// filter chips, table, dialogs. Scoped under .inv2 so it composes with the
// app shell; visual system matches the SKU instrument and the run desk.
export const INV_CSS = `
.inv2{--card:#FFFFFF;--panel:#FAFBFC;--line:#E7EAEF;--line2:#F1F3F6;--ink:#0F172A;--ink2:#334155;--muted:#5B6472;--faint:#8A93A2;--ghost:#C2C8D2;--accent:#4F46E5;--accent-ink:#3730A3;--accent-soft:#EEF0FE;--accent-line:#D5D8F7;--good:#15803D;--good-soft:#EAF6EE;--amber:#B45309;--amber-soft:#FCF3E6;--red:#C0362C;--red-soft:#FBEBEA;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.45;color:var(--ink)}
.inv2 *{box-sizing:border-box}
.inv2 h1{font-size:20px;font-weight:750;letter-spacing:-.015em;margin:0;color:var(--ink)}
.inv2 .btn{font-family:inherit;font-size:12px;font-weight:600;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink2);cursor:pointer;display:inline-flex;align-items:center;gap:7px;text-decoration:none;white-space:nowrap;transition:border-color .12s,background .12s}
.inv2 .btn:hover:not(:disabled){border-color:var(--ghost);background:var(--panel)}
.inv2 .btn.pri{color:#fff;background:var(--accent);border-color:var(--accent)}
.inv2 .btn.pri:hover:not(:disabled){background:var(--accent-ink)}
.inv2 .btn.danger{color:#fff;background:var(--red);border-color:var(--red)}
.inv2 .btn.sm{font-size:11px;padding:4px 10px}
.inv2 .btn:disabled{opacity:.45;cursor:not-allowed}
.inv2 .lnk{color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none;background:none;border:none;padding:0;font-family:inherit;font-size:inherit}
.inv2 .mono{font-family:var(--mono)}
.inv2 .sub{font-size:11px;color:var(--faint)}
.inv2 .kbd{font-family:var(--mono);font-size:9.5px;color:var(--muted);background:var(--line2);border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:1px 5px}
.inv2 .chip{display:inline-flex;align-items:center;gap:5px;font-size:9.5px;font-weight:700;padding:2px 7px;border-radius:99px;border:1px solid;white-space:nowrap}
.inv2 .chip.ok{color:var(--good);background:var(--good-soft);border-color:#CDE8D6}
.inv2 .chip.neu{color:var(--muted);background:var(--line2);border-color:var(--line)}
.inv2 .chip.warn{color:var(--amber);background:var(--amber-soft);border-color:#F3E0BE}
.inv2 .chip.bad{color:var(--red);background:var(--red-soft);border-color:#F1CDC9}
.inv2 .chip.acc{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}

.inv2 .scope{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--card)}
.inv2 .scope button{font-family:inherit;font-size:11.5px;font-weight:650;padding:6px 13px;border:none;background:var(--card);color:var(--muted);border-right:1px solid var(--line2);cursor:pointer}
.inv2 .scope button:last-child{border-right:none}
.inv2 .scope button.on{background:var(--accent);color:#fff}

.inv2 .att{display:grid;grid-template-columns:repeat(4,1fr) 1.15fr;gap:9px;margin-top:12px}
@media(max-width:1080px){.inv2 .att{grid-template-columns:repeat(2,1fr)}}
.inv2 .acard{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:9px 12px;cursor:pointer;text-align:left;font-family:inherit;transition:border-color .12s}
.inv2 .acard:hover{border-color:var(--accent-line)}
.inv2 .acard.on{border-color:var(--accent);background:var(--accent-soft)}
.inv2 .acard.hot{border-color:#F1CDC9;background:#FEF7F6}
.inv2 .acard.hot.on{border-color:var(--red)}
.inv2 .acard .an{font-size:19px;font-weight:750;color:var(--ink);line-height:1.15;font-variant-numeric:tabular-nums}
.inv2 .acard.hot .an{color:var(--red)}
.inv2 .acard .al{font-size:10.5px;color:var(--muted);margin-top:1px;line-height:1.35}
.inv2 .acard .ax{font-size:9.5px;color:var(--faint);margin-top:3px}
.inv2 .acard.quiet{background:var(--panel);border-style:dashed;cursor:default}
.inv2 .acard.quiet:hover{border-color:var(--line)}
.inv2 .acard.quiet .an{font-size:13.5px;color:var(--faint);line-height:1.4}

.inv2 .views{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-top:14px}
.inv2 .views button{font-family:inherit;font-size:12.5px;font-weight:650;padding:8px 14px;border:none;background:none;color:var(--faint);border-bottom:2px solid transparent;margin-bottom:-1px;cursor:pointer}
.inv2 .views button.on{color:var(--ink);border-bottom-color:var(--accent)}

.inv2 .tools{display:flex;gap:8px;align-items:center;margin-top:11px;flex-wrap:wrap}
.inv2 .search{flex:1;min-width:190px;display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:9px;padding:0 11px;background:var(--card)}
.inv2 .search input{border:none;outline:none;padding:7px 0;font-family:inherit;font-size:12.5px;width:100%;background:transparent;color:var(--ink)}
.inv2 .search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.10)}
.inv2 .fbtn{font-family:inherit;font-size:11.5px;font-weight:650;color:var(--ink2);background:var(--card);border:1px solid var(--line);padding:7px 12px;border-radius:9px;display:inline-flex;gap:6px;align-items:center;cursor:pointer}
.inv2 .fbtn.on{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-soft)}
.inv2 .fbtn .cnt{background:var(--accent);color:#fff;border-radius:99px;font-size:9px;padding:0 5px;font-weight:750}
.inv2 .fchip{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:600;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:99px;padding:3px 9px;cursor:pointer}
.inv2 .fchip .x{color:var(--accent);font-weight:800}

.inv2 .tblcard{background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden;margin-top:10px}
.inv2 table{border-collapse:collapse;width:100%;font-size:12px}
.inv2 thead th{font-size:9.5px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);background:var(--panel);white-space:nowrap;position:sticky;top:0;z-index:2}
.inv2 thead th.r{text-align:right}
.inv2 thead th.sortable{cursor:pointer;user-select:none}
.inv2 thead th.sortable:hover{color:var(--ink2)}
.inv2 thead th .arw{opacity:.45;margin-left:3px}
.inv2 thead th.sorted{color:var(--accent-ink)}
.inv2 thead th.sorted .arw{opacity:1}
.inv2 tbody td{padding:7px 10px;border-bottom:1px solid var(--line2);vertical-align:middle}
.inv2 tbody td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.inv2 tbody tr:last-child td{border-bottom:none}
.inv2 tbody tr:hover{background:var(--panel)}
.inv2 tbody tr.focus{background:var(--accent-soft)}
.inv2 tbody tr.sel{background:#F7F8FF}
.inv2 .pname{font-weight:650;color:var(--ink);font-size:12.5px;display:block;line-height:1.3;cursor:pointer}
.inv2 .pname:hover{color:var(--accent)}
.inv2 .pmeta{font-size:10.5px;color:var(--faint);margin-top:1px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.inv2 .pmeta .sku{font-family:var(--mono)}
.inv2 .good{color:var(--good);font-weight:700}
.inv2 .amber{color:var(--amber);font-weight:700}
.inv2 .red{color:var(--red);font-weight:700}
.inv2 .zero{color:var(--ghost)}
.inv2 .cover{position:relative;height:7px;background:var(--line2);border-radius:4px;width:52px;display:inline-block;vertical-align:middle;margin-right:6px}
.inv2 .cover i{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.inv2 .cover b{position:absolute;top:-2px;bottom:-2px;width:1.5px;background:var(--ink);border-radius:1px}
.inv2 .tfoot{padding:9px 12px;border-top:1px solid var(--line2);font-size:11px;color:var(--faint);display:flex;gap:10px;align-items:center;background:var(--panel);flex-wrap:wrap}
.inv2 .selbar{display:flex;gap:12px;align-items:center;padding:9px 12px;background:var(--accent-soft);border-bottom:1px solid var(--accent-line);font-size:11.5px;color:var(--accent-ink);flex-wrap:wrap}
.inv2 .empty{padding:38px 20px;text-align:center;color:var(--muted);font-size:13px}
.inv2 .empty .et{font-size:14.5px;font-weight:700;color:var(--ink);margin-bottom:5px}
.inv2 .skel{height:9px;border-radius:5px;background:linear-gradient(90deg,var(--line2) 25%,#E9ECF3 37%,var(--line2) 63%);background-size:400% 100%;animation:inv2sk 1.3s ease-in-out infinite}
@keyframes inv2sk{0%{background-position:100% 50%}100%{background-position:0 50%}}
@media(prefers-reduced-motion:reduce){.inv2 .skel{animation:none}}

.inv2-ovl{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:1000;display:flex;align-items:flex-start;justify-content:center;padding:52px 18px;overflow-y:auto}
.inv2 .dlg{background:#fff;border-radius:13px;box-shadow:0 20px 50px rgba(0,0,0,.25);width:100%;overflow:hidden}
@media(prefers-reduced-motion:no-preference){.inv2 .dlg{animation:inv2pop .15s ease-out}}
@keyframes inv2pop{from{transform:translateY(8px);opacity:.5}to{transform:none;opacity:1}}
.inv2 .dh{padding:15px 18px 11px;border-bottom:1px solid var(--line2)}
.inv2 .dt{font-size:15.5px;font-weight:750;color:var(--ink)}
.inv2 .dsub{font-size:11.5px;color:var(--faint);margin-top:3px;line-height:1.5}
.inv2 .db{padding:14px 18px;max-height:56vh;overflow-y:auto}
.inv2 .df{padding:12px 18px;border-top:1px solid var(--line2);display:flex;gap:9px;align-items:center;background:var(--panel);flex-wrap:wrap}
.inv2 .flab{font-size:9.5px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);display:block;margin-bottom:5px}
.inv2 .fin{font-family:inherit;border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:12px;background:#fff;color:var(--ink);width:100%;outline:none}
.inv2 .fin:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.12)}
.inv2 .seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;flex-wrap:wrap}
.inv2 .seg button{font-family:inherit;font-size:11px;font-weight:650;padding:5px 11px;border:none;background:#fff;color:var(--muted);border-right:1px solid var(--line2);cursor:pointer}
.inv2 .seg button:last-child{border-right:none}
.inv2 .seg button.on{background:var(--accent-soft);color:var(--accent-ink)}
.inv2 .pickcard{border:1px solid var(--line);border-radius:9px;padding:10px 12px;cursor:pointer;background:#fff}
.inv2 .pickcard.on{border-color:var(--accent);background:var(--accent-soft)}
.inv2 .pickcard .pt{font-weight:700;font-size:12px;color:var(--ink)}
.inv2 .drop{border:1.5px dashed var(--accent-line);background:var(--accent-soft);border-radius:10px;padding:20px;text-align:center;color:var(--muted);font-size:12.5px}
.inv2 .drop.over{border-color:var(--accent);background:#E7EAFE}
.inv2 .diff{border:1px solid var(--line);border-radius:9px;overflow:hidden;font-size:11.5px}
.inv2 .diffhead{padding:5px 11px;background:var(--panel);font-size:10.5px;font-weight:750;color:var(--muted);border-bottom:1px solid var(--line2)}
.inv2 .diffrow{display:grid;grid-template-columns:100px 92px 1fr;gap:9px;padding:6px 11px;border-bottom:1px solid var(--line2);align-items:baseline}
.inv2 .diffrow:last-child{border-bottom:none}
.inv2 .menu{position:absolute;right:0;top:calc(100% + 5px);background:#fff;border:1px solid var(--line);border-radius:11px;box-shadow:0 12px 30px rgba(15,23,42,.14);padding:5px;width:300px;z-index:50}
.inv2 .mi{display:flex;gap:9px;align-items:flex-start;padding:8px 9px;border-radius:8px;font-size:12px;color:var(--ink2);cursor:pointer;text-align:left;border:none;background:none;width:100%;font-family:inherit}
.inv2 .mi:hover{background:var(--panel)}
.inv2 .mi .mt{font-weight:650;color:var(--ink)}
.inv2 .mi .mx{font-size:10.5px;color:var(--faint);margin-top:1px;line-height:1.4}
/* The Filters button sits at the right of the toolbar, so the panel opens
   leftward — anchoring it left pushed 342px off the viewport edge. Below the
   toolbar's wrap point it becomes a bottom sheet, which can never clip. */
.inv2 .fpanel{position:absolute;right:0;left:auto;top:calc(100% + 5px);background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 14px 36px rgba(15,23,42,.16);padding:0;width:560px;max-width:calc(100vw - 32px);z-index:50}
@media(max-width:820px){
  .inv2 .fpanel{position:fixed;top:auto;bottom:0;left:0;right:0;width:auto;max-width:none;border-radius:14px 14px 0 0;box-shadow:0 -12px 40px rgba(15,23,42,.22);max-height:80vh;overflow-y:auto}
}
.inv2 .fgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.inv2 .fgrid{grid-template-columns:1fr}}
.inv2 .shorto{position:fixed;inset:0;background:rgba(15,23,42,.5);z-index:1200;display:flex;align-items:center;justify-content:center}
.inv2 .shorto .box{background:#fff;border-radius:14px;padding:20px 24px;width:360px;box-shadow:0 20px 50px rgba(0,0,0,.3)}
.inv2 .shorto .krow{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line2);font-size:12.5px}
`
