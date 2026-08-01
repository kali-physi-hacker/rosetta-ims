// Shared stylesheet for the SKU detail surfaces (classic /items page and the
// /sku domain-lanes view) — one type scale, card system, and badge language.
export const SKUD_CSS = `
.skud{--card:#FFFFFF;--panel:#FAFBFC;--line:#E7EAEF;--line2:#F1F3F6;--ink:#0F172A;--ink2:#334155;--muted:#5B6472;--faint:#8A93A2;--ghost:#C2C8D2;--accent:#4F46E5;--accent-ink:#3730A3;--accent-soft:#EEF0FE;--accent-line:#D5D8F7;--good:#15803D;--good-soft:#EAF6EE;--amber:#B45309;--amber-soft:#FCF3E6;--red:#C0362C;--red-soft:#FBEBEA;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.45;color:var(--ink)}
.skud *{box-sizing:border-box}
.skud .btn{font-family:inherit;font-size:12.5px;font-weight:600;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink2);cursor:pointer;transition:border-color .12s,background .12s;display:inline-flex;align-items:center;gap:7px;text-decoration:none}
.skud .btn:hover{border-color:var(--ghost);background:var(--panel)}
.skud .btn.pri{color:#fff;background:var(--accent);border-color:var(--accent)}
.skud .btn.pri:hover{background:var(--accent-ink)}
.skud .btn.danger{color:var(--red);border-color:#EAB4AF}
.skud .btn.danger:hover{background:var(--red-soft)}
.skud .hdr{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;align-items:flex-start;margin-bottom:16px}
.skud .eyebrow{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);font-weight:600;margin-bottom:7px}
.skud .eyebrow .cd{width:8px;height:8px;border-radius:50%}
.skud h1{font-size:23px;font-weight:680;letter-spacing:-0.015em;margin:0 0 8px;line-height:1.2;color:var(--ink)}
.skud .idline{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
.skud .idline .sep{color:var(--ghost)}
.skud .skucode{font-family:var(--mono);color:var(--ink2)}
.skud .lnk{color:var(--accent);cursor:pointer;font-weight:600}
.skud .idgrid{display:flex;gap:30px;flex-wrap:wrap;align-items:flex-start}
.skud .idlabel{font-size:10px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}
.skud .idrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.skud .idrow .skucode{font-size:14px}
.skud .idrow .lnk{font-size:11.5px}
.skud .supsku{font-family:var(--mono);font-size:14px;font-weight:600;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:7px;padding:5px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;line-height:1;transition:border-color .12s,background .12s}
.skud .supsku:hover{border-color:var(--accent)}
.skud .supsku .cpi{color:var(--accent);opacity:.65}
.skud .supsku.copied{color:var(--good);background:var(--good-soft);border-color:#CDE8D6}
.skud .supsku .cf{font-family:inherit;font-size:11px;font-weight:700}
.skud .idsup{font-size:12.5px;color:var(--muted)}
.skud .idnone{font-size:13px;color:var(--faint);font-style:italic}
.skud .cmp{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 17px;border-bottom:1px solid var(--line2)}
.skud .cmp-nm{font-size:13px;font-weight:600;color:var(--accent);text-decoration:none}
.skud .cmp-nm:hover{text-decoration:underline}
.skud .cmp-meta{font-size:11px;color:var(--faint);margin-top:2px}
.skud .cmp-price{display:flex;align-items:center;gap:9px;flex-shrink:0}
.skud .cmp-p{font-size:14px;font-weight:700;color:var(--ink2);font-variant-numeric:tabular-nums}
.skud .cmp-p.cheap{color:var(--good)}
.skud .cmp-x{border:none;background:none;color:var(--ghost);cursor:pointer;font-size:16px;line-height:1;padding:2px 5px;border-radius:5px}
.skud .cmp-x:hover{color:var(--red);background:var(--red-soft)}
.skud .cmpe{padding:15px 17px;font-size:12px;color:var(--faint)}
.skud .cmp-add{display:flex;gap:8px;padding:12px 17px;border-top:1px solid var(--line2);background:var(--panel)}
.skud .cmp-in{font-family:inherit;font-size:12.5px;padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--ink);outline:none}
.skud .cmp-in.url{flex:1;min-width:0}
.skud .cmp-in.nm{width:150px}
.skud .cmp-in:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.skud .cmpwrap{display:flex;align-items:stretch}
.skud .cmptabs{display:flex;flex-direction:column;flex-shrink:0;width:180px;border-right:1px solid var(--line);max-height:360px;overflow-y:auto}
.skud .cmptab{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 13px;border:none;border-left:2px solid transparent;border-bottom:1px solid var(--line2);background:none;cursor:pointer;text-align:left;font-family:inherit}
.skud .cmptab:hover{background:var(--panel)}
.skud .cmptab.on{background:var(--accent-soft);border-left-color:var(--accent)}
.skud .cmptab .tnm{font-size:12px;font-weight:600;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.skud .cmptab.on .tnm{color:var(--accent-ink)}
.skud .cmptab .tpr{font-size:12px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums;flex-shrink:0}
.skud .cmptab .tpr.cheap{color:var(--good)}
.skud .cmppanel{flex:1;min-width:0;padding:13px 17px}
.skud .cpp-h{display:flex;align-items:center;justify-content:space-between;gap:8px}
.skud .cpp-lead{font-size:12.5px;color:var(--ink2);margin:11px 0 7px}
.skud .cpptab{width:100%;border-collapse:collapse}
.skud .cpptab th{text-align:right;font-size:10px;font-weight:650;color:var(--faint);text-transform:uppercase;letter-spacing:.02em;padding:3px 8px}
.skud .cpptab th:first-child{text-align:left}
.skud .cpptab td{padding:6px 8px;border-top:1px solid var(--line2);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums}
.skud .cpptab td.cpp-row{text-align:left;color:var(--ink2);font-weight:600}
.skud .cpptab .cpp-sub{font-weight:400;color:var(--faint);font-size:11px}
.skud .badges{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
.skud .bdg{font-size:11px;font-weight:650;padding:3px 10px;border-radius:6px;display:inline-flex;align-items:center;gap:5px;border:1px solid}
.skud .bdg.ok{background:var(--good-soft);color:var(--good);border-color:#CDE8D6}
.skud .bdg.neu{background:var(--line2);color:var(--muted);border-color:var(--line)}
.skud .bdg.acc{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.skud .bdg.warn{background:var(--amber-soft);color:var(--amber);border-color:#F3E0BE}
.skud .bdg .st{width:7px;height:7px;border-radius:50%;background:currentColor}
.skud .hdr-act{display:flex;gap:8px;flex-wrap:wrap}
.skud .alert{display:flex;align-items:center;gap:11px;padding:11px 15px;border-radius:10px;margin-bottom:14px;font-size:13px}
.skud .alert.warn{background:var(--amber-soft);border:1px solid #F3E0BE;color:#7A4A12}
.skud .alert.red{background:var(--red-soft);border:1px solid #F1CDC9;color:#7A2A24}
.skud .alert .ad{width:9px;height:9px;border-radius:50%;background:var(--amber);flex:none}
.skud .alert.red .ad{background:var(--red)}
.skud .alert b{color:var(--amber)}
.skud .alert.red b{color:var(--red)}
.skud .metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:22px}
.skud .metric{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px 15px}
.skud .metric .ml{font-size:11px;color:var(--muted);font-weight:600;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.skud .metric .mv{font-size:19px;font-weight:680;letter-spacing:-0.01em;font-variant-numeric:tabular-nums}
.skud .metric .ms{font-size:11px;color:var(--faint);margin-top:4px}
.skud .metric .ms.good{color:var(--good)}
.skud .metric .ms.amber{color:var(--amber)}
@media(max-width:1080px){.skud .metrics{grid-template-columns:repeat(3,1fr)}}
.skud .grid{display:grid;grid-template-columns:1.62fr 1fr;gap:16px;align-items:start}
@media(max-width:980px){.skud .grid{grid-template-columns:1fr}}
.skud .col{display:flex;flex-direction:column;gap:16px;min-width:0}
.skud .card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.skud .ch{padding:13px 17px;border-bottom:1px solid var(--line2);display:flex;align-items:center;justify-content:space-between;gap:10px}
.skud .ct{font-size:13px;font-weight:650}
.skud .ch .hint{font-size:11px;color:var(--faint)}
.skud .cb{padding:15px 17px}
.skud .cb.flush{padding:0}
.skud table{border-collapse:collapse;width:100%}
.skud .mtab th{text-align:right;font-size:10px;font-weight:650;color:#4A5462;text-transform:uppercase;letter-spacing:.02em;padding:9px 12px;border-bottom:1px solid var(--line2);white-space:nowrap}
.skud .mtab th:first-child{text-align:left}
.skud .mtab td{padding:11px 12px;border-bottom:1px solid var(--line2);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums}
.skud .mtab td:first-child{text-align:left}
.skud .mtab tr:last-child td{border-bottom:none}
.skud .cstat{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:11px}
.skud .cstat .d{width:7px;height:7px;border-radius:50%}
.skud .cstat.on{color:var(--good)}
.skud .cstat.on .d{background:var(--good)}
.skud .cstat.off{color:var(--faint)}
.skud .cstat.off .d{background:var(--ghost)}
.skud .gpv{font-weight:700}
.skud .gpv.good{color:var(--good)}
.skud .gpv.warn{color:var(--amber)}
.skud .gpv.bad{color:var(--red)}
.skud .soldas{font-size:10.5px;color:var(--faint);margin-top:2px;text-align:left}
.skud .mbbline{padding:10px 17px;background:var(--accent-soft);border-bottom:1px solid var(--line2);font-size:12px;color:var(--accent-ink);font-weight:600;display:flex;justify-content:space-between}
.skud .legend{padding:11px 17px;font-size:11px;color:var(--muted);line-height:1.55;border-top:1px solid var(--line2);background:var(--panel)}
.skud .legend b{color:var(--ink2)}
.skud .kv{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-bottom:1px solid var(--line2);font-size:12.5px}
.skud .kv:last-child{border-bottom:none}
.skud .kv .k{color:var(--muted)}
.skud .kv .v{color:var(--ink);font-weight:600;text-align:right}
.skud .kv .v.acc{color:var(--accent-ink)}
.skud .stockrow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:15px}
.skud .stbox{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;text-align:center}
.skud .stbox .n{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.skud .stbox .l{font-size:11px;color:var(--muted);margin-top:3px}
.skud .spark{display:flex;align-items:flex-end;gap:8px;height:74px;padding:6px 0 2px}
.skud .spark .bcol{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px}
.skud .spark .bar{width:100%;background:var(--accent-soft);border-radius:4px 4px 0 0;position:relative;min-height:6px}
.skud .spark .bar b{position:absolute;inset:0;background:var(--accent);border-radius:4px 4px 0 0;opacity:.9}
.skud .spark .bl{font-size:10.5px;color:var(--faint)}
.skud .wocbar{height:8px;border-radius:5px;background:var(--line2);overflow:hidden;margin:6px 0 4px}
.skud .wocbar b{display:block;height:100%;border-radius:5px}
.skud .sim{margin-top:15px;padding:14px;border:1px dashed var(--accent-line);border-radius:10px;background:var(--accent-soft)}
.skud .sim .sl{font-size:11px;font-weight:700;color:var(--accent-ink);text-transform:uppercase;letter-spacing:.04em;margin-bottom:9px}
.skud .simrow{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.skud .simrow input{width:84px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-family:inherit;font-size:13px;outline:none}
.skud .simrow input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.14)}
.skud .chip{font-size:11.5px;font-weight:600;color:var(--ink2);background:#fff;border:1px solid var(--line);border-radius:99px;padding:6px 11px;cursor:pointer}
.skud .chip:hover{border-color:var(--accent-line);color:var(--accent-ink)}
.skud .simout{margin-top:11px;font-size:13px;color:var(--ink2)}
.skud .simout b{color:var(--good)}
.skud .costrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px;margin-bottom:14px}
.skud .costrow .pill{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 11px;font-weight:600}
.skud .costrow .pill.land{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.skud .costrow .op{color:var(--faint);font-weight:700}
.skud .subh{font-size:10.5px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;margin:6px 0 8px}
.skud .miniform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.skud .miniform input,.skud .miniform select{font-family:inherit;font-size:12px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;outline:none;background:#fff}
.skud .term{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 11px;border:1px solid var(--line);border-radius:9px;margin-bottom:7px;font-size:12.5px}
.skud .term .tl b{color:var(--ink)}
.skud .term .tl span{color:var(--muted)}
.skud .term .tr{font-weight:700;font-variant-numeric:tabular-nums;display:flex;align-items:center}
.skud .best{font-size:12px;color:var(--ink2);margin-top:4px}
.skud .best b{color:var(--accent-ink)}
.skud .sup{padding:15px 17px;border-bottom:1px solid var(--line2)}
.skud .sup:last-child{border-bottom:none}
.skud .sup-h{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.skud .sup-nm{font-size:14px;font-weight:650;display:flex;align-items:center;gap:8px}
.skud .prefflag{font-size:9.5px;font-weight:700;color:var(--good);background:var(--good-soft);padding:1px 6px;border-radius:4px}
.skud .sup-meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:3px}
.skud .sup-cost{text-align:right;flex:none}
.skud .sup-cost .c{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}
.skud .sup-cost .u{font-size:10.5px;color:var(--faint)}
.skud .sstat{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;margin-top:8px}
.skud .sstat.ok{background:var(--good-soft);color:var(--good)}
.skud .sstat.oos{background:var(--red-soft);color:var(--red)}
.skud .sstat .d{width:6px;height:6px;border-radius:50%;background:currentColor}
.skud .oosdetail{margin-top:11px;padding:11px 13px;background:var(--red-soft);border:1px solid #F1CDC9;border-radius:9px}
.skud .oosdetail .kv{border-color:#F1CDC9;padding:6px 0}
.skud .oosdetail .kv .k{color:#9A3B33}
.skud .oosdetail .kv .v{color:#7A2A24}
.skud .oosdetail summary{margin-top:8px;font-size:11.5px;font-weight:650;color:var(--accent);cursor:pointer}
.skud .histrow{display:flex;justify-content:space-between;padding:6px 0;border-top:1px solid #F1CDC9;font-size:11.5px}
.skud .histrow .hd{color:#7A2A24}
.skud .histrow .hv{font-weight:700}
.skud .linkbtn{font-family:inherit;font-size:11.5px;font-weight:650;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
.skud .plat{font-size:9px;font-weight:800;padding:2px 6px;border-radius:4px;border:1px solid;margin-left:4px}
.skud .plat.on{background:var(--good-soft);color:var(--good);border-color:#CDE8D6}
.skud .plat.off{background:var(--line2);color:var(--faint);border-color:var(--line)}
.skud .tagchip{display:inline-block;font-size:11px;color:var(--ink2);background:var(--line2);border:1px solid var(--line);border-radius:99px;padding:3px 10px;margin:0 5px 5px 0}
.skud .hitl{margin-top:12px;padding:11px 13px;background:var(--amber-soft);border:1px solid #F3E0BE;border-radius:9px;font-size:11.5px;color:#7A4A12}
.skud .hitl .hr{display:flex;gap:8px;margin-top:9px}
.skud .hitl input{flex:1;font-family:inherit;font-size:12px;padding:7px 9px;border:1px solid #E7CFA0;border-radius:7px;outline:none;min-width:0}
.skud .hitl button{font-family:inherit;font-size:12px;font-weight:650;color:#fff;background:var(--good);border:none;border-radius:7px;padding:7px 12px;cursor:pointer}
.skud .aud{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--line2)}
.skud .aud:last-child{border-bottom:none}
.skud .aud .ad{width:8px;height:8px;border-radius:50%;background:var(--ghost);margin-top:5px;flex:none}
.skud .aud .at{font-size:12.5px;color:var(--ink2)}
.skud .aud .at b{color:var(--ink);font-weight:650}
.skud .aud .aw{font-size:10.5px;color:var(--faint);margin-top:2px}
.skud .footbar{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
.skud .footbar .l,.skud .footbar .r{display:flex;gap:9px;flex-wrap:wrap}
`

// Rev-04 "instrument" layer for the /sku view: status ticks, decision strip,
// offering ledger, bullet bars, price ladder, simulator, drawers & dialogs.
// Scoped under .skud so it composes with SKUD_CSS.
export const SKUD2_CSS = `
.skud .ticks{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card)}
.skud .tick{display:flex;flex-direction:column;gap:2px;padding:8px 15px;border-right:1px solid var(--line2);min-width:96px}
.skud .tick:last-child{border-right:none}
.skud .tick .tl{font-size:9.5px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.skud .tick .tv{font-size:12.5px;font-weight:700;color:var(--ink);display:flex;gap:6px;align-items:center;white-space:nowrap}
.skud .tick .ic{width:14px;height:14px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;color:#fff;flex:none}
.skud .ic.g{background:#22A55E}.skud .ic.a{background:#D97706}.skud .ic.r{background:#DC2626}
.skud .kbd{font-family:var(--mono);font-size:10px;color:var(--muted);background:var(--line2);border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:1px 6px}
.skud .btn.soon{color:var(--faint);background:var(--line2);border-color:var(--line);cursor:not-allowed;user-select:none;white-space:nowrap}
.skud .btn.soon:hover{background:var(--line2);border-color:var(--line)}
.skud .soonchip{font-style:normal;font-size:8px;font-weight:800;letter-spacing:.07em;color:var(--faint);background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 4px;line-height:1.5}
.skud .strip{display:grid;grid-template-columns:1.02fr 22px 1.35fr 22px 1fr 22px 1.5fr;align-items:stretch;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:16px}
@media(max-width:1080px){.skud .strip{grid-template-columns:1fr 1fr;gap:0}.skud .strip .conn{display:none}}
.skud .strip .blk{padding:12px 15px;display:flex;flex-direction:column;gap:4px;min-width:0}
.skud .strip .bl{font-size:9.5px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);white-space:nowrap}
.skud .strip .bv{font-size:19px;font-weight:720;color:var(--ink);line-height:1.15;font-variant-numeric:tabular-nums}
.skud .strip .bv .u{font-size:11px;color:var(--faint);font-weight:550}
.skud .strip .bs{font-size:11px;color:var(--muted);display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.skud .strip .conn{display:flex;align-items:center;justify-content:center;color:var(--ghost);font-size:15px;background:linear-gradient(to right,var(--card),var(--panel))}
.skud .strip .blk.act{background:var(--accent-soft);border-left:1px solid var(--accent-line)}
.skud .strip .blk.act .bv{font-size:13px;line-height:1.4;font-weight:600;color:var(--ink2)}
.skud .strip .blk.act .bv b{color:var(--accent-ink)}
.skud .srcchip{font-size:8.5px;font-weight:750;letter-spacing:.04em;padding:1px 6px;border-radius:99px;border:1px solid}
.skud .srcchip.catalogue{color:var(--good);background:var(--good-soft);border-color:#CDE8D6}
.skud .srcchip.manual{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}
.skud .bull{position:relative;height:10px;background:var(--line2);border-radius:5px;flex:1;min-width:90px}
.skud .bull .ghost{position:absolute;top:0;bottom:0;left:0;background:#DFE2EE;border-radius:5px}
.skud .bull .fill{position:absolute;top:0;bottom:0;left:0;border-radius:5px}
.skud .bull .fill.g{background:#34B36F}.skud .bull .fill.a{background:#E9A23B}.skud .bull .fill.r{background:#E25A4E}
.skud .bull .floor{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);border-radius:1px}
.skud .bull .rival{position:absolute;top:-7px;width:0;height:0;border-left:4.5px solid transparent;border-right:4.5px solid transparent;border-top:6px solid #7A4FE5;transform:translateX(-4.5px)}
.skud .cover{position:relative;height:10px;background:var(--line2);border-radius:5px;min-width:100px}
.skud .cover .fill{position:absolute;top:0;bottom:0;left:0;background:#34B36F;border-radius:5px}
.skud .cover .fill.low{background:#E9A23B}
.skud .cover .fill.crit{background:#E25A4E}
.skud .cover .proj{position:absolute;top:0;bottom:0;background:repeating-linear-gradient(45deg,#B9E3CC 0 4px,#D9EFE2 4px 8px);border-radius:0 5px 5px 0}
.skud .cover .tgt{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);border-radius:1px}
.skud .lrow{display:grid;grid-template-columns:20px minmax(120px,1.3fr) minmax(80px,.9fr) auto 76px auto auto 22px;gap:10px;align-items:center;padding:10px 15px;border-bottom:1px solid var(--line2);cursor:pointer;background:var(--card)}
.skud .lrow:hover{background:var(--panel)}
.skud .lrow.open{background:#FBFBFE}
.skud .lrow .star{color:var(--accent);font-size:13px;text-align:center}
.skud .lrow .nm{font-weight:700;color:var(--ink);font-size:13px}
.skud .lrow .cost{font-weight:750;font-size:14px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.skud .lrow .cost .u{font-size:10px;color:var(--faint);font-weight:550}
.skud .lrow .chev{color:var(--faint);text-align:center}
.skud .lex{background:#FBFBFE;border-bottom:1px solid var(--line2);padding:13px 15px 15px 45px}
.skud .lex .cols{display:grid;grid-template-columns:1.25fr 1fr;gap:20px}
@media(max-width:980px){.skud .lex .cols{grid-template-columns:1fr}}
.skud .lex .kv2{display:flex;gap:10px;font-size:12px;padding:3px 0;color:var(--ink2)}
.skud .lex .kv2 .k{flex:0 0 78px;font-size:9.5px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);padding-top:3px}
.skud .ladder{position:relative;height:38px;margin:4px 0 2px}
.skud .ladder .rail{position:absolute;left:0;right:0;top:24px;height:2px;background:var(--line)}
.skud .ladder .step{position:absolute;top:0;transform:translateX(-50%);text-align:center;cursor:pointer;white-space:nowrap}
.skud .ladder .step .pv{font-size:11.5px;font-weight:750;color:var(--ink);font-variant-numeric:tabular-nums}
.skud .ladder .step .pq{font-size:9px;color:var(--faint);white-space:nowrap}
.skud .ladder .step i{display:block;width:8px;height:8px;border-radius:50%;background:var(--card);border:2px solid var(--accent);margin:2px auto 0}
.skud .ladder .step.hit i{background:var(--accent)}
.skud .ladder .you{position:absolute;top:21px;width:2px;height:12px;background:var(--ink);transform:translateX(-1px)}
.skud .ladder .you::after{content:"you";position:absolute;top:12px;left:-9px;font-size:8.5px;color:var(--muted)}
.skud .tml{position:relative;padding-left:15px}
.skud .tml::before{content:"";position:absolute;left:3.5px;top:5px;bottom:5px;width:1.5px;background:var(--line)}
.skud .tev{position:relative;padding:3px 0 9px;font-size:12px;color:var(--ink2)}
.skud .tev::before{content:"";position:absolute;left:-15px;top:7px;width:8px;height:8px;border-radius:50%;background:var(--card);border:2px solid var(--accent-line)}
.skud .tev.cur::before{border-color:#22A55E;background:#22A55E}
.skud .tev .tw{color:var(--faint);font-size:10.5px}
.skud .tev .srcfile{border-bottom:1px dotted currentColor;cursor:help}
.skud .issue{display:flex;gap:9px;align-items:flex-start;background:var(--amber-soft);border:1px solid #F3E0BE;border-radius:9px;padding:8px 12px;font-size:12px;color:#6B4A12;margin:8px 15px}
.skud .issue .ad{width:7px;height:7px;border-radius:50%;background:var(--amber);margin-top:5px;flex:none}
.skud .issue b{color:var(--amber)}
.skud .bmrow{display:grid;grid-template-columns:minmax(84px,1fr) 86px minmax(100px,1.2fr) 64px 64px;gap:10px;align-items:center;padding:9px 15px;border-bottom:1px solid var(--line2)}
.skud .bmrow .chn{font-weight:650;color:var(--ink);font-size:12.5px}
.skud .bmrow .prc{text-align:right;font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap}
.skud .stepper{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--card)}
.skud .stepper b{padding:4px 13px;font-size:13.5px;font-variant-numeric:tabular-nums}
.skud .stepper button{border:none;background:var(--panel);padding:5px 11px;font-size:13px;color:var(--ink2);cursor:pointer;border-left:1px solid var(--line2);border-right:1px solid var(--line2);font-family:inherit}
.skud .stepper button:hover{background:var(--line2)}
.skud .slider{position:relative;height:44px;margin:12px 2px 0}
.skud .slider .ticklab.b2{top:auto;bottom:0}
.skud .slider input[type=range]{position:absolute;inset:0;width:100%;opacity:0;cursor:pointer;z-index:3;margin:0}
.skud .slider .rail{position:absolute;left:0;right:0;top:13px;height:4px;background:var(--line2);border-radius:2px}
.skud .slider .fillr{position:absolute;left:0;top:13px;height:4px;background:var(--accent);border-radius:2px}
.skud .slider .knob{position:absolute;top:6px;width:18px;height:18px;border-radius:50%;background:var(--card);border:2px solid var(--accent);box-shadow:0 1px 3px rgba(15,23,42,.18);transform:translateX(-9px);z-index:2}
.skud .slider .tickm{position:absolute;top:10px;width:2px;height:10px;background:var(--accent-line);transform:translateX(-1px)}
.skud .slider .ticklab{position:absolute;top:-9px;transform:translateX(-50%);font-size:8.5px;color:var(--faint);white-space:nowrap}
.skud .simbox{background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:9px;padding:8px 12px;font-size:12px;color:var(--ink2);margin-top:8px}
.skud .ovl{position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:1000;display:flex;align-items:flex-start;justify-content:center;padding:60px 18px;overflow-y:auto}
.skud-drawer{position:fixed;top:0;right:0;bottom:0;width:440px;max-width:94vw;background:#fff;z-index:1001;box-shadow:-18px 0 50px rgba(15,23,42,.22);overflow-y:auto;padding:20px 22px}
@media(prefers-reduced-motion:no-preference){.skud-drawer{animation:skudSlide .16s ease-out}}
@keyframes skudSlide{from{transform:translateX(30px);opacity:.4}to{transform:none;opacity:1}}
.skud .dlg{background:#fff;border-radius:14px;width:520px;max-width:100%;padding:20px 22px;box-shadow:0 20px 50px rgba(0,0,0,.25)}
.skud .flab{font-size:10px;font-weight:700;color:var(--faint);display:block;margin-bottom:4px;letter-spacing:.03em;text-transform:uppercase}
.skud .fin{font-family:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:7px;padding:7px 10px;background:#fff;color:var(--ink);width:100%;outline:none}
.skud .fin:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.12)}
.skud .preview{background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:8px;padding:9px 12px;font-size:12px;color:var(--ink2)}
.skud .activity{display:flex;gap:22px;padding:10px 15px;font-size:12px;color:var(--ink2);flex-wrap:wrap;align-items:baseline}
.skud .activity .tw{color:var(--faint);font-size:10.5px}
.skud .shorto{position:fixed;inset:0;background:rgba(15,23,42,.5);z-index:1200;display:flex;align-items:center;justify-content:center}
.skud .shorto .box{background:#fff;border-radius:14px;padding:22px 26px;width:340px;box-shadow:0 20px 50px rgba(0,0,0,.3)}
.skud .shorto .row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line2);font-size:13px}
.skud .shapegrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.skud .shape{border:1px solid var(--line);border-radius:9px;padding:9px 12px;cursor:pointer;background:var(--card);transition:border-color .12s,background .12s}
.skud .shape:hover{border-color:var(--accent-line)}
.skud .shape.on{border-color:var(--accent);background:var(--accent-soft)}
.skud .shape.off{opacity:.45;cursor:not-allowed}
.skud .shape .sn{font-size:12.5px;font-weight:700;color:var(--ink)}
.skud .shape .sd{font-size:10.5px;color:var(--muted);margin-top:2px;line-height:1.4}
.skud .dealcard{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:10px 13px;margin-bottom:8px;background:var(--card)}
.skud .dealcard.editing{border-color:var(--accent);background:var(--accent-soft)}
.skud .dealcard .ds{font-size:13px;color:var(--ink);font-weight:600}
.skud .dealcard .ds b{font-variant-numeric:tabular-nums}
.skud .dealcard .dm{font-size:11px;color:var(--muted);margin-top:3px}
.skud .kindchip{font-size:8.5px;font-weight:750;letter-spacing:.04em;padding:1px 7px;border-radius:99px;border:1px solid;white-space:nowrap}
.skud .kindchip.fx{color:var(--muted);background:var(--line2);border-color:var(--line)}
.skud .kindchip.rel{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}
.skud .sentence{font-size:13.5px;color:var(--ink2);line-height:2.15;margin-top:4px}
.skud .sentence .si{font-family:inherit;font-size:13px;font-weight:650;border:1px solid var(--line);border-radius:7px;padding:4px 8px;background:#fff;color:var(--ink);outline:none;text-align:right;width:76px;margin:0 3px;vertical-align:baseline}
.skud .sentence select.si{width:auto;text-align:left;font-weight:600}
.skud .sentence .si:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.12)}
.skud .normline{font-size:10.5px;color:var(--faint);margin-top:2px}
.skud .lockline{font-size:10.5px;color:var(--faint);background:var(--line2);border:1px solid var(--line);border-radius:7px;padding:5px 9px;margin-top:8px}
@media(prefers-reduced-motion:no-preference){
  .skud .lex{animation:skudFade .14s ease-out}
  @keyframes skudFade{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
}
`
