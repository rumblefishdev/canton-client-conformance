#!/usr/bin/env python3
"""results.json -> a self-contained HTML report: divergences first, then the matrix, then every cell.

Usage: python3 report/render.py [--in report/out/results.json] [--out report/out/index.html] [--public]
--public leaves out private scenarios and cases (findings not yet seen by the SDK maintainers).
"""
import argparse, json, re
from html import escape as e
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = {"pass": "✅", "fail": "❌", "diverges": "⚠️", "unsupported": "➖", "n/a": "·", "error": "🛑"}
WORD = {"pass": "pass", "fail": "fail", "diverges": "diverges", "unsupported": "unsupported", "n/a": "n/a",
        "error": "harness error"}
SEVERITY = ["error", "fail", "diverges", "unsupported", "pass", "n/a"]

CSS = """
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;color:#1f2328;background:#f6f8fa}
main{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}h3{font-size:16px;margin:0}
.muted{color:#656d76}.small{font-size:12px}
.card{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:16px;margin:12px 0}
.banner{background:#fff1e5;border:1px solid #d4a72c;border-radius:8px;padding:10px 14px;margin:12px 0}
table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:6px 8px;border-top:1px solid #eaeef2}
th{font-weight:600;background:#f6f8fa}
.v{display:inline-block;padding:1px 8px;border-radius:12px;font-size:12px;font-weight:600;white-space:nowrap}
.v-pass{background:#dafbe1;color:#116329}.v-fail{background:#ffebe9;color:#a40e26}.v-diverges{background:#fff8c5;color:#7d4e00}
.v-unsupported,.v-n\\/a{background:#eaeef2;color:#57606a}.v-error{background:#fbefff;color:#6639ba}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:4px;margin-left:4px;vertical-align:1px}
.known{background:#eaeef2;color:#57606a}.unexpected{background:#a40e26;color:#fff}.private{background:#d4a72c;color:#fff}
.chip{display:inline-block;font:11px ui-monospace,monospace;background:#eef1f4;border-radius:4px;padding:1px 5px;margin:2px 2px 0 0}
.ev{font-size:12px;color:#424a53;margin-top:3px;word-break:break-word}
.matrix td.cell{text-align:center;white-space:nowrap}.matrix a{color:inherit;text-decoration:none}
.stats{display:flex;gap:12px;flex-wrap:wrap}.stat{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:10px 14px;min-width:170px}
.stat b{font-size:20px}
details summary{cursor:pointer;color:#0969da;font-size:12px}
"""


def md(text):
    """Escape, then render `code` spans."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text or ""))


def badge(v):
    return f'<span class="v v-{e(v)}">{ICON.get(v, "")} {e(WORD.get(v, v))}</span>'


def tags(cell, private=False):
    out = ""
    if cell.get("status") == "unexpected":
        out += f'<span class="tag unexpected" title="expected {e(cell["expected"])}">unexpected</span>'
    elif cell["verdict"] not in ("pass", "n/a") and cell.get("finding"):
        out += '<span class="tag known">known</span>'
    if private:
        out += '<span class="tag private">private</span>'
    return out


def evidence(text, limit=260):
    text = text or ""
    if len(text) <= limit:
        return f'<div class="ev">{e(text)}</div>'
    return f'<div class="ev">{e(text[:limit])}… <details><summary>more</summary>{e(text)}</details></div>'


def worst(verdicts):
    vs = [v for v in verdicts if v != "n/a"] or ["n/a"]
    return min(vs, key=SEVERITY.index)


def filter_public(data):
    scenarios = []
    for s in data["scenarios"]:
        if s.get("private"):
            continue
        cases = [c for c in s["cases"] if not c.get("private")]
        if cases:
            scenarios.append({**s, "cases": cases})
    return {**data, "scenarios": scenarios}


def render(data, public=False):
    if public:
        data = filter_public(data)
    lanes, env = data["lanes"], data["env"]
    sdks = {"ts": "TypeScript (DA)", "rust": "Rust (Nodejumper)"}
    has_private = any(s.get("private") or any(c.get("private") for c in s["cases"]) for s in data["scenarios"])
    cells = [(s, c, l, c["cells"][l]) for s in data["scenarios"] for c in s["cases"] for l in s["lanes"] if l in c["cells"]]

    h = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Canton SDK conformance – {e(data["run"])}</title>'
         f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body><main>']
    h.append('<h1>Canton Ledger API SDK conformance</h1>')
    ver = " · ".join(x for x in [
        f'Canton {e(env["canton"])}' if env.get("canton") else "",
        f'canton-localnet {e(env["localnet"])}' if env.get("localnet") else "",
        *[f"{e(sdks.get(k, k))}: <code>{e(v)}</code>" for k, v in sorted(env.get("sdks", {}).items()) if v]] if x)
    h.append(f'<div class="muted">Run {e(data["run"])} UTC · {ver}</div>')
    h.append('<div class="muted small">Each cell compares an SDK with the reference adapter (raw JSON Ledger API) or a '
             'fixed oracle. ✅ pass · ❌ fail (wrong result) · ⚠️ diverges (correct but different, e.g. a loud error where '
             'others succeed, or a policy difference) · ➖ unsupported · <span class="tag known">known</span> recorded '
             'divergence with a finding · <span class="tag unexpected">unexpected</span> differs from what the suite '
             'expects.</div>')
    if has_private:
        h.append('<div class="banner"><b>Private: do not publish.</b> This report includes findings the SDK maintainers '
                 'have not seen yet (marked <span class="tag private">private</span>). Render with '
                 '<code>--public</code> before sharing.</div>')

    # headline numbers per SDK
    h.append('<div class="stats">')
    for sdk, name in sdks.items():
        mine = [x for x in cells if lanes[x[2]]["sdk"] == sdk and x[3]["verdict"] != "n/a"]
        if not mine:
            continue
        n = lambda v: sum(1 for x in mine if x[3]["verdict"] == v)
        unexpected = sum(1 for x in mine if x[3]["status"] == "unexpected")
        h.append(f'<div class="stat"><div class="muted small">{e(name)}</div><b>{n("pass")}</b> pass · '
                 f'<b>{n("fail")}</b> fail · <b>{n("diverges")}</b> diverge'
                 + (f'<div class="small" style="color:#a40e26">{unexpected} unexpected</div>' if unexpected else
                    '<div class="small muted">all as expected</div>') + '</div>')
    h.append('</div>')

    # divergences showcase
    div = [x for x in cells if x[3]["verdict"] in ("fail", "diverges", "error")]
    div.sort(key=lambda x: (SEVERITY.index(x[3]["verdict"]), x[0]["id"], x[1]["id"]))
    h.append(f'<h2>Divergences ({len(div)})</h2><div class="card"><table><tr><th>Scenario</th><th>SDK</th>'
             '<th>Verdict</th><th>What happened</th></tr>')
    for s, c, l, cell in div:
        priv = s.get("private") or c.get("private")
        h.append(f'<tr><td><a href="#{e(s["id"])}">{e(s["id"])}</a> {md(c["title"])}</td><td>{e(lanes[l]["label"])}</td>'
                 f'<td>{badge(cell["verdict"])}{tags(cell, priv)}</td><td>{evidence(cell["evidence"])}'
                 + (f'<div class="ev muted">{md(cell["finding"])}</div>' if cell.get("finding") else "") + '</td></tr>')
    if not div:
        h.append('<tr><td colspan="4" class="muted">None in this run.</td></tr>')
    h.append('</table></div>')

    # matrix
    h.append('<h2>Matrix</h2><div class="card"><table class="matrix"><tr><th>Scenario</th>'
             + "".join(f"<th>{e(n)}</th>" for n in sdks.values()) + '<th>Standard rows</th></tr>')
    for s in data["scenarios"]:
        row = f'<tr><td><a href="#{e(s["id"])}"><b>{e(s["id"])}</b> {md(s["title"])}</a>' \
              + (' <span class="tag private">private</span>' if s.get("private") else "") + '</td>'
        for sdk in sdks:
            mine = [c["cells"][l] for c in s["cases"] for l in s["lanes"] if lanes[l]["sdk"] == sdk and l in c["cells"]]
            if not mine:
                row += '<td class="cell muted">–</td>'
                continue
            counted = [m for m in mine if m["verdict"] != "n/a"]
            ok = sum(1 for m in counted if m["verdict"] == "pass")
            w = worst(m["verdict"] for m in mine)
            flag = ' <span class="tag unexpected">unexpected</span>' if any(m["status"] == "unexpected" for m in mine) else ""
            row += f'<td class="cell">{badge(w)}<div class="small muted">{ok}/{len(counted)} pass</div>{flag}</td>'
        row += '<td>' + "".join(f'<span class="chip">{e(r)}</span>' for r in s.get("standard") or []) + '</td></tr>'
        h.append(row)
    h.append('</table></div>')

    # details
    h.append('<h2>Scenarios</h2>')
    for s in data["scenarios"]:
        h.append(f'<div class="card" id="{e(s["id"])}"><h3>{e(s["id"])} · {md(s["title"])}'
                 + (' <span class="tag private">private</span>' if s.get("private") else "") + '</h3>')
        if s.get("summary"):
            h.append(f'<p class="muted">{md(s["summary"])}</p>')
        meta = "".join(f'<span class="chip">{e(r)}</span>' for r in s.get("standard") or [])
        refs = " · ".join(md(f) for f in s.get("findings") or [])
        h.append(f'<div class="small">{meta}</div><div class="small muted">{refs} · <code>{e(s["file"])}</code></div>')
        h.append('<table><tr><th>Case</th>' + "".join(f'<th>{e(lanes[l]["label"])}</th>' for l in s["lanes"]) + '</tr>')
        for c in s["cases"]:
            row = f'<tr><td>{md(c["title"])}' + (' <span class="tag private">private</span>' if c.get("private") and not s.get("private") else "") + '</td>'
            for l in s["lanes"]:
                cell = c["cells"].get(l)
                if not cell:
                    row += '<td class="muted">–</td>'
                    continue
                row += f'<td>{badge(cell["verdict"])}{tags(cell)}{evidence(cell["evidence"], 180)}'
                if cell.get("data", {}).get("mismatches"):
                    row += '<details><summary>vectors</summary><div class="ev">' + "<br>".join(
                        e(m) for m in cell["data"]["mismatches"]) + '</div></details>'
                if cell.get("verifies"):
                    row += '<div class="small muted">verifies ' + ", ".join(e(r) for r in cell["verifies"]) + '</div>'
                row += '</td>'
            h.append(row + '</tr>')
        h.append('</table></div>')
    h.append('<p class="muted small">Generated by report/render.py from report/out/results.json. Every cell has a raw '
             'transcript under report/out/runs/.</p></main></body></html>')
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", type=Path, default=ROOT / "report/out/results.json")
    ap.add_argument("--out", type=Path, default=ROOT / "report/out/index.html")
    ap.add_argument("--public", action="store_true")
    a = ap.parse_args()
    a.out.write_text(render(json.loads(a.src.read_text()), public=a.public))
    print(a.out)


if __name__ == "__main__":
    main()
