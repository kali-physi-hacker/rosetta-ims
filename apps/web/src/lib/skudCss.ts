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
