#!/usr/bin/env python3
"""Render coverage/coverage.json into a self-contained heatmap page: coverage/index.html."""
import html, json
from datetime import date
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
cov = json.loads((ROOT / "coverage" / "coverage.json").read_text())
caps = yaml.safe_load((ROOT / "standard" / "capabilities.yaml").read_text())

LABEL = {
    "verified": ("Verified", "Behaviour verified by a test run"),
    "claimed": ("Claimed", "The SDK declares this row in its own machine-readable conformance registry"),
    "found": ("Evidence", "Code or docs referencing this capability found in the SDK (heuristic, not a conformance result)"),
    "partial": ("Partial", "Hand review found building blocks only, not the whole row (hover for what is missing)"),
    "not_found": ("No evidence", "Nothing found by the scan; may exist under other names. Corrections welcome"),
    "not_provided": ("Out of scope", "The library does not include this layer"),
    "unassessed": ("Not assessed", "Cannot be judged by static scan; needs manual review or a live test"),
}
ORDER = ["verified", "claimed", "found", "partial", "not_found", "not_provided", "unassessed"]
esc = html.escape
meta = {s["key"]: s for s in yaml.safe_load((ROOT / "tools" / "coverage" / "sdks.yaml").read_text())}
sdks = [s for s in cov["sdks"] if meta[s["key"]].get("poc")]
later = [s for s in cov["sdks"] if not meta[s["key"]].get("poc")]
required = [c for c in caps if c["requirement"] == "Required"]


def pct(sdk):
    have = sum(1 for c in required if sdk["cells"][c["id"]]["status"] in ("verified", "claimed", "found"))
    part = sum(1 for c in required if sdk["cells"][c["id"]]["status"] == "partial")
    return have, round(100 * have / len(required)), part, round(100 * part / len(required))


def snote(s):
    return f'<div class="meta snote">{esc(s["note"])}</div>' if s.get("note") else ""


head_cells = "".join(
    f'<th scope="col"><div class="sdk">{esc(s["name"])}</div><div class="meta">{esc(s["repo"])} @ {esc(s["head"].split()[0])}</div>{snote(s)}</th>'
    for s in sdks)
summary_cells = "".join(
    f'<td><div class="bar"><span style="width:{pct(s)[1]}%"></span><i style="width:{pct(s)[3]}%"></i></div><div class="meta">{pct(s)[0]}/{len(required)} Required rows with evidence, +{pct(s)[2]} partial</div></td>'
    for s in sdks)

body, dom = [], None
for c in caps:
    if c["domain"] != dom:
        dom = c["domain"]
        body.append(f'<tr class="domain"><th colspan="{len(sdks) + 1}" scope="rowgroup">{esc(dom)}</th></tr>')
    req = "" if c["requirement"] == "Required" else ' <span class="tag">Upcoming</span>'
    tds = []
    for s in sdks:
        cell = s["cells"][c["id"]]
        st = cell["status"]
        tip = [LABEL[st][0] + ": " + LABEL[st][1]]
        if cell.get("review"):
            tip.append("Review: " + cell["review"])
        if cell.get("evidence"):
            tip.append("Evidence: " + "; ".join(cell["evidence"]))
        tds.append(f'<td class="c {st}" title="{esc(chr(10).join(tip))}"><span>{esc(LABEL[st][0])}</span></td>')
    body.append(f'<tr><th scope="row" title="{esc(c["description"])}">{esc(c["name"])}{req}</th>{"".join(tds)}</tr>')

later_html = "".join(f'<li><b>{esc(s["name"])}</b> <span class="mut">({esc(s["repo"])})</span>: {esc(meta[s["key"]].get("later_reason", ""))}</li>' for s in later)
legend = "".join(f'<li><span class="sw c {k}"></span><b>{esc(LABEL[k][0])}</b>: {esc(LABEL[k][1])}</li>' for k in ORDER)

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canton SDK Coverage Map</title>
<style>
:root{{--bg:#fbfbfa;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e3df;--head:#f2f1ee;
--verified:#1f7a4d;--claimed:#2f9e6a;--found:#9fd8b6;--partial:#f2e2a6;--not_found:#f3c9c2;--not_provided:#e6e5e1;--unassessed:#fff;--ink-dark:#fff;--ink:#1d1d1b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#161615;--fg:#ecebe7;--mut:#a3a29c;--line:#2e2e2b;--head:#1f1f1d;
--verified:#2f9e6a;--claimed:#2a8a5c;--found:#2c5a41;--partial:#5c4f22;--not_found:#6a3a33;--not_provided:#2a2a27;--unassessed:#161615;--ink:#ecebe7}}}}
:root[data-theme="dark"]{{--bg:#161615;--fg:#ecebe7;--mut:#a3a29c;--line:#2e2e2b;--head:#1f1f1d;
--verified:#2f9e6a;--claimed:#2a8a5c;--found:#2c5a41;--partial:#5c4f22;--not_found:#6a3a33;--not_provided:#2a2a27;--unassessed:#161615;--ink:#ecebe7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1400px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}}p{{margin:6px 0;max-width:900px}}.mut{{color:var(--mut)}}
.note{{border-left:3px solid var(--mut);padding:8px 12px;background:var(--head);margin:14px 0;max-width:900px}}
.wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:6px;margin-top:16px}}
table{{border-collapse:collapse;width:100%;min-width:980px}}
th,td{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:middle}}
thead th{{position:sticky;top:0;background:var(--head);z-index:1;vertical-align:bottom;font-weight:600}}
tbody th{{font-weight:500;white-space:nowrap}}.sdk{{font-size:13px}}.snote{{max-width:150px;white-space:normal;font-style:italic}}.meta{{font-size:11px;color:var(--mut);font-weight:400}}
tr.domain th{{background:var(--head);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);padding-top:10px}}
td.c{{font-size:11px;text-align:center;color:var(--ink);cursor:help;min-width:92px}}
td.c.verified,td.c.claimed{{color:#fff}}td.c.unassessed span{{color:var(--mut)}}
.verified{{background:var(--verified)}}.claimed{{background:var(--claimed)}}.found{{background:var(--found)}}
.partial{{background:var(--partial)}}.not_found{{background:var(--not_found)}}.not_provided{{background:var(--not_provided)}}.unassessed{{background:var(--unassessed)}}
.tag{{font-size:10px;border:1px solid var(--line);border-radius:3px;padding:0 4px;color:var(--mut);margin-left:4px}}
.bar{{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-bottom:3px}}.bar{{display:flex}}.bar span{{display:block;height:100%;background:var(--claimed)}}.bar i{{display:block;height:100%;background:var(--partial)}}
ul.legend{{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:6px 18px;margin:14px 0}}
.sw{{display:inline-block;width:14px;height:14px;border:1px solid var(--line);vertical-align:-2px;margin-right:6px;border-radius:2px}}
</style></head><body><main>
<h1>Canton SDK Coverage Map</h1>
<p class="mut">The {len(caps)} rows of the <a href="https://docs.canton.network/sdks-tools/language-bindings/client-standard">Ledger Client Standard</a>
({len(required)} Required, {len(caps) - len(required)} Upcoming; cf-docs, last changed 2026-10-20) against the {len(sdks)} Canton client SDKs in scope for the proof of concept. Generated {date.today().isoformat()}.</p>
<div class="note"><b>This is a desk survey, not a conformance result.</b> Cells come from a static scan of each SDK's hand-written source for
evidence of each capability, plus the SDK's own self-declared claims where they exist, plus hand review (hover a cell for the evidence and notes).
"No evidence" means our scan found nothing. It may exist under another name. Maintainer corrections are very welcome.
Live behavioural checks (the "Verified" state) come next, from the cross-SDK conformance runner.</div>
<ul class="legend">{legend}</ul>
<div class="wrap"><table>
<thead><tr><th scope="col">Capability</th>{head_cells}</tr>
<tr><th scope="row" class="meta">Required rows (evidence + partial)</th>{summary_cells}</tr></thead>
<tbody>{"".join(body)}</tbody></table></div>
<p class="mut" style="margin-top:14px">Method and data: <code>tools/coverage/</code> (patterns.yaml, sdks.yaml, overrides.yaml) → <code>coverage/coverage.json</code>.
</p>
<h2 style="font-size:16px;margin:28px 0 6px">Coming later</h2>
<p class="mut">Scanned, but not hand-reviewed yet, so not shown. Each needs only a new adapter; the scenarios and runner stay the same.</p>
<ul>{later_html}</ul>
<p class="mut">Capability IDs follow the <code>section__row</code> convention of nodejumper-org/canton-rust-sdk.</p>
</main></body></html>"""
(ROOT / "coverage" / "index.html").write_text(page)
print("wrote coverage/index.html", {s["key"]: pct(s) for s in sdks})
