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
.rdesk .laneh .lc{font-size:11px;color:var(--muted)}
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
`
