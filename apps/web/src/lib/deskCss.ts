// Run-desk stylesheet (catalogue review rev-04): lifecycle rail, decision
// lanes, staged-changes dock, focus overlay. Scoped under .rdesk; visual
// system matches the SKU instrument (same tokens, 13px base, hairlines).
export const DESK_CSS = `
.rdesk{--card:#FFFFFF;--panel:#FAFBFC;--line:#E7EAEF;--line2:#F1F3F6;--ink:#0F172A;--ink2:#334155;--muted:#5B6472;--faint:#8A93A2;--ghost:#C2C8D2;--accent:#4F46E5;--accent-ink:#3730A3;--accent-soft:#EEF0FE;--accent-line:#D5D8F7;--good:#15803D;--good-soft:#EAF6EE;--amber:#B45309;--amber-soft:#FCF3E6;--red:#C0362C;--red-soft:#FBEBEA;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.45;color:var(--ink)}
.rdesk *{box-sizing:border-box}
.rdesk .btn{font-family:inherit;font-size:12px;font-weight:600;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink2);cursor:pointer;display:inline-flex;align-items:center;gap:7px;text-decoration:none;white-space:nowrap;transition:border-color .12s,background .12s}
.rdesk .btn:hover{border-color:var(--ghost);background:var(--panel)}
.rdesk .btn.pri{color:#fff;background:var(--accent);border-color:var(--accent)}
.rdesk .btn.pri:hover{background:var(--accent-ink)}
.rdesk .btn.sm{font-size:11px;padding:4px 10px}
.rdesk .btn:disabled{opacity:.5;cursor:not-allowed}
.rdesk .lnk{color:var(--accent);cursor:pointer;font-weight:600;text-decoration:none}
.rdesk .mono{font-family:var(--mono)}
.rdesk .bdg{font-size:10px;font-weight:700;padding:2px 8px;border-radius:99px;display:inline-flex;align-items:center;gap:5px;border:1px solid;white-space:nowrap}
.rdesk .bdg.ok{background:var(--good-soft);color:var(--good);border-color:#CDE8D6}
.rdesk .bdg.neu{background:var(--line2);color:var(--muted);border-color:var(--line)}
.rdesk .bdg.acc{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.rdesk .bdg.warn{background:var(--amber-soft);color:var(--amber);border-color:#F3E0BE}
.rdesk .bdg.bad{background:var(--red-soft);color:var(--red);border-color:#F1CDC9}
.rdesk .bdg .st{width:6px;height:6px;border-radius:50%;background:currentColor}
.rdesk .kbd{font-family:var(--mono);font-size:9.5px;color:var(--muted);background:var(--line2);border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:1px 5px}
.rdesk h1{font-size:19px;font-weight:750;letter-spacing:-.01em;margin:0;color:var(--ink)}

/* lifecycle rail */
.rdesk .life{display:flex;align-items:stretch;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:12px}
.rdesk .stage{display:flex;align-items:center;gap:8px;padding:8px 14px;font-size:11.5px;font-weight:650;color:var(--faint);border-right:1px solid var(--line2);white-space:nowrap}
.rdesk .stage:last-child{border-right:none}
.rdesk .stage .sdot{min-width:16px;height:16px;border-radius:99px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;color:#fff;background:var(--ghost);flex:none;padding:0 4px}
.rdesk .stage.done{color:var(--good)}
.rdesk .stage.done .sdot{background:#22A55E}
.rdesk .stage.now{color:var(--ink);background:var(--accent-soft)}
.rdesk .stage.now .sdot{background:var(--accent)}
.rdesk .stage .scount{font-weight:450;color:var(--faint)}

/* issue banner */
.rdesk .issue{display:flex;gap:9px;align-items:flex-start;background:var(--amber-soft);border:1px solid #F3E0BE;border-radius:10px;padding:8px 13px;font-size:12px;color:#6B4A12;margin-top:10px}
.rdesk .issue .ad{width:7px;height:7px;border-radius:50%;background:#D97706;margin-top:5px;flex:none}
.rdesk .issue b{color:var(--amber)}

/* layout */
.rdesk .desk{display:grid;grid-template-columns:1fr 272px;gap:12px;align-items:start;margin-top:12px}
@media(max-width:1080px){.rdesk .desk{grid-template-columns:1fr}}
.rdesk .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}

/* lanes */
.rdesk .lane{border-bottom:1px solid var(--line)}
.rdesk .lane:last-child{border-bottom:none}
.rdesk .laneh{display:flex;align-items:center;gap:10px;padding:9px 14px;background:var(--panel);border-bottom:1px solid var(--line2);flex-wrap:wrap}
.rdesk .laneh .ln{font-size:12.5px;font-weight:750;color:var(--ink)}
/* One clipped line, never three wrapped ones: Clean's caption names membership,
   decided count and spot-check progress, which is long enough to dominate the
   header on a narrow window. The row itself still wraps when the controls no
   longer fit, which is the right trade at that width. */
.rdesk .laneh .lc{font-size:11px;color:var(--muted);flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rdesk .lanebody{max-height:264px;overflow-y:auto}
.rdesk .trow{display:grid;grid-template-columns:76px minmax(0,1.9fr) 92px minmax(0,1.2fr) auto;gap:10px;align-items:center;padding:7px 14px;border-bottom:1px solid var(--line2);font-size:12px}
.rdesk .trow:last-child{border-bottom:none}
.rdesk .trow:hover{background:var(--panel)}
.rdesk .trow .sku{font-family:var(--mono);color:var(--muted);font-size:11px;white-space:nowrap}
.rdesk .trow .nm{color:var(--ink);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.rdesk .trow .prc{text-align:right;font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap;font-size:11.5px}
.rdesk .trow .why{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rdesk .trow.decided{opacity:.5}
.rdesk .trow.decided .nm{text-decoration:line-through;text-decoration-color:var(--ghost)}
.rdesk .clusterrow{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1.2fr) auto;gap:10px;align-items:center;padding:8px 14px;border-bottom:1px solid var(--line2);font-size:12px}
.rdesk .clusterrow:hover{background:var(--panel)}

/* dock */
.rdesk .dock{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;position:sticky;top:12px}
.rdesk .ring{position:relative;width:72px;height:72px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none}
.rdesk .ring::after{content:"";position:absolute;inset:9px;background:var(--card);border-radius:50%}
.rdesk .ring b{position:relative;z-index:1;font-size:14px;color:var(--ink);font-variant-numeric:tabular-nums}
.rdesk .dstat{display:flex;gap:6px;align-items:center;font-size:11px;color:var(--muted);padding:1.5px 0;white-space:nowrap}
.rdesk .dstat i{width:8px;height:8px;border-radius:2px;display:inline-block;flex:none}
.rdesk .dsec{border-top:1px solid var(--line2);padding:9px 13px}
.rdesk .dsec .dh{font-size:9.5px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);margin-bottom:5px}
.rdesk .sitem{display:flex;gap:7px;align-items:baseline;font-size:11.5px;padding:2.5px 0;color:var(--ink2)}
.rdesk .sitem .sku{font-family:var(--mono);font-size:10.5px;color:var(--faint);flex:none}
.rdesk .sitem b{font-variant-numeric:tabular-nums}
.rdesk .fin{font-family:inherit;font-size:12px;border:1px solid var(--line);border-radius:7px;padding:6px 9px;background:#fff;color:var(--ink);outline:none;width:100%}
.rdesk .fin:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.12)}

/* focus overlay */
.rdesk-ovl{position:fixed;inset:0;background:rgba(15,23,42,.5);z-index:1000;display:flex;align-items:flex-start;justify-content:center;padding:34px 18px;overflow-y:auto}
.rdesk-focus{background:#F4F5F8;border-radius:14px;width:1060px;max-width:100%;box-shadow:0 24px 60px rgba(0,0,0,.3);padding:16px 18px}
@media(prefers-reduced-motion:no-preference){.rdesk-focus{animation:rdeskPop .16s ease-out}}
@keyframes rdeskPop{from{transform:translateY(8px);opacity:.5}to{transform:none;opacity:1}}
.rdesk .fgrid{display:grid;grid-template-columns:1.15fr 1fr;gap:12px;align-items:start;margin-top:10px}
@media(max-width:900px){.rdesk .fgrid{grid-template-columns:1fr}}
.rdesk .ev{border-collapse:collapse;width:100%;font-size:12px}
.rdesk .ev td{padding:6px 12px;border-bottom:1px solid var(--line2);vertical-align:top}
.rdesk .ev td.k{color:var(--faint);width:132px;font-size:10.5px;padding-top:8px}
.rdesk .ev td.v{color:var(--ink);font-weight:600;white-space:pre-wrap;word-break:break-word}
.rdesk .ev tr:last-child td{border-bottom:none}
.rdesk .readas{padding:8px 12px;border-top:1px solid var(--line2);background:var(--accent-soft);font-size:11.5px;color:var(--accent-ink)}
.rdesk .sugg{border:1px solid var(--line);border-radius:10px;padding:8px 11px;margin-bottom:7px;cursor:pointer;background:var(--card)}
.rdesk .sugg:hover{border-color:var(--accent-line)}
.rdesk .sugg.on{border-color:var(--accent);background:var(--accent-soft)}
.rdesk .sugg .nm{font-weight:700;color:var(--ink);font-size:12px}
.rdesk .sugg .meta{font-size:10.5px;color:var(--muted);margin-top:2px;font-family:var(--mono)}
.rdesk .chiprow{display:flex;gap:6px;flex-wrap:wrap}
.rdesk .rchip{font-size:10.5px;font-weight:650;padding:3px 10px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--muted);cursor:pointer}
.rdesk .rchip.on{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-ink)}
.rdesk .prog{height:5px;border-radius:3px;background:var(--line2);overflow:hidden;min-width:90px}
.rdesk .prog i{display:block;height:100%;background:var(--accent);border-radius:3px}
/* Draft-a-new-product panel. It replaces the decision rail rather than sitting
   beside it — creating a canonical product is a different job from picking one,
   and doing both at once is how duplicates get made. */
.rdesk .cdh{display:flex;align-items:baseline;gap:10px;padding:9px 13px;border-bottom:1px solid var(--line2);background:var(--accent-soft);font-size:12.5px;font-weight:700;color:var(--accent-ink)}
.rdesk .cdnote{font-size:10.5px;font-weight:500;color:var(--muted);margin-left:auto}
.rdesk .cdgrid{display:grid;grid-template-columns:1fr 1.2fr;gap:16px;padding:12px 13px}
@media(max-width:820px){.rdesk .cdgrid{grid-template-columns:1fr}}
.rdesk .cdlab{font-size:9.5px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.rdesk .cdev{display:flex;justify-content:space-between;gap:12px;padding:4px 0;border-bottom:1px solid var(--line2);font-size:11.5px;color:var(--muted)}
.rdesk .cdev:last-child{border-bottom:none}
.rdesk .cdev b{color:var(--ink);font-weight:650;text-align:right;min-width:0;overflow-wrap:anywhere}
.rdesk .cdf{display:block;margin-bottom:8px}
.rdesk .cdf>span{display:block;font-size:9.5px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}
.rdesk .cdf .fin{width:100%}
.rdesk .cdsku{font-size:11px;color:var(--faint);background:var(--panel);border:1px dashed var(--line);border-radius:7px;padding:6px 9px}
.rdesk .cdsku b{color:var(--ink2);letter-spacing:.08em}
.rdesk .cdradar{margin:0 13px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:9px 11px}
.rdesk .cdradar.ok{border-color:#CDE8D6;background:var(--good-soft)}
.rdesk .cdradar.warn{border-color:#F3E0BE;background:var(--amber-soft)}
.rdesk .cdradar.bad{border-color:#F1CDC9;background:var(--red-soft)}
.rdesk .cdrh{display:flex;align-items:baseline;gap:8px;font-size:12px;color:var(--ink2)}
.rdesk .cdrs{font-size:10.5px;color:var(--muted);margin-top:4px;line-height:1.5}
.rdesk .cddup{display:flex;align-items:center;gap:9px;padding:5px 0;font-size:11.5px;color:var(--ink2);border-bottom:1px solid rgba(15,23,42,.06)}
.rdesk .cddup:last-child{border-bottom:none}
/* Historical rows carry a whole product name in sku_code, so this must clamp
   or it eats the row and the name has nowhere to go. */
.rdesk .cddup .mono{font-size:10.5px;color:var(--faint);flex:0 1 auto;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rdesk .cddup .dn{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rdesk .badsku{font-size:8.5px;font-weight:750;color:var(--amber);background:var(--amber-soft);border:1px solid #F3E0BE;border-radius:99px;padding:0 5px;flex:none}
.rdesk .cdbar{height:5px;width:46px;border-radius:3px;background:rgba(15,23,42,.09);position:relative;overflow:hidden;flex:none}
.rdesk .cdbar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px}
.rdesk .cdact{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:10px 13px;border-top:1px solid var(--line2)}
.rdesk .willcreate{display:inline-flex;align-items:center;gap:4px;font-size:9.5px;font-weight:750;padding:1px 7px;border-radius:99px;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);white-space:nowrap}
/* Run search — finds a row in any lane, including the two that are collapsed
   by default, so the lane chip has to say where it came from. */
.rdesk .dsearch{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:8px;padding:0 9px;background:#fff;min-width:280px}
.rdesk .dsearch:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.10)}
.rdesk .dsearch svg{color:var(--faint);flex:none}
.rdesk .dsearch input{border:none;outline:none;background:transparent;font-family:inherit;font-size:12px;color:var(--ink);padding:6px 0;width:100%}
.rdesk .dsearch .x{border:none;background:none;color:var(--faint);font-size:15px;line-height:1;cursor:pointer;padding:0 2px;font-family:inherit}
.rdesk .dsearch .x:hover{color:var(--ink2)}
/* The filename opens the document it names. */
/* Removing a staged row is deliberate, not decorative: quiet until hovered,
   then plainly destructive. */
.rdesk .srow .unstage{font-family:inherit;font-size:10.5px;color:var(--faint);background:none;border:none;padding:2px 4px;cursor:pointer;border-radius:5px}
.rdesk .srow .unstage:hover{color:var(--red);background:var(--redbg,#FEF2F2)}
.rdesk .srcfile{font-family:inherit;font-size:inherit;color:inherit;background:none;border:none;padding:0;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:2px}
.rdesk .srcfile:hover{color:var(--accent)}
.rdesk .lanetag{display:inline-block;font-size:9px;font-weight:750;letter-spacing:.03em;padding:1px 6px;border-radius:99px;border:1px solid;margin-right:7px;white-space:nowrap;vertical-align:1px}
.rdesk .lanetag.pick{color:var(--amber);background:var(--amber-soft);border-color:#F3E0BE}
.rdesk .lanetag.new{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}
.rdesk .lanetag.check{color:var(--red);background:var(--red-soft);border-color:#F1CDC9}
.rdesk .lanetag.clean{color:var(--muted);background:var(--line2);border-color:var(--line)}
@media(max-width:720px){.rdesk .dsearch{min-width:160px}}
/* Staged review — every row publish will touch, grouped by what it does. */
.rdesk .sgroupchip{font-size:11px;font-weight:600;background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 10px;white-space:nowrap}
.rdesk .sgroupchip b{font-weight:750}
.rdesk .sgrouph{position:sticky;top:0;z-index:1;padding:6px 13px;background:var(--panel);border-bottom:1px solid var(--line2);font-size:9.5px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.rdesk .srow{display:flex;align-items:center;gap:11px;padding:7px 13px;border-bottom:1px solid var(--line2);font-size:12px}
.rdesk .srow:last-child{border-bottom:none}
.rdesk .srow .sku{font-size:11px;color:var(--muted);flex:none;width:82px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rdesk .srow .nm{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
.rdesk .srow .ssup{flex:none;width:72px;font-size:10.5px;color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rdesk .srow .sval{flex:none;width:196px;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.rdesk .srow .sdelta{flex:none;width:52px;text-align:right;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}
.rdesk .srow .lnk{flex:none;white-space:nowrap;width:42px;text-align:right}
@media(max-width:720px){.rdesk .srow .ssup{display:none}}
`