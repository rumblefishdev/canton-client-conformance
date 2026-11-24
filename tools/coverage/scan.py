#!/usr/bin/env python3
"""Static coverage scan: Ledger Client Standard rows x SDKs.

Desk research, not conformance. For each SDK it greps hand-written source for evidence of each
capability (patterns.yaml), folds in the SDK's own machine-readable claims where they exist, then
applies hand-reviewed overrides (overrides.yaml). Output: coverage.json.

Usage: scan.py --repos DIR   (DIR/<repo-name> must be a checkout of each repo in sdks.yaml)
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def load(p):
    with open(p) as f:
        return yaml.safe_load(f)


def source_files(base, sdk):
    excl = [re.compile(x) for x in sdk.get("exclude", [])]
    exts = tuple("." + e for e in sdk["ext"])
    for root in sdk["roots"]:
        for dp, _, fns in os.walk(base / root):
            for fn in fns:
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, base)
                if fn.endswith(exts) and not any(x.search("/" + rel) for x in excl):
                    yield rel


def rust_claims(base, rel):
    txt = (base / rel).read_text()
    return set(re.findall(r'^id\s*=\s*"([^"]+)"', txt, re.M))


LIVE_KEYS = {"ts": "ts", "rust": "rust_nodejumper"}


def live_verified(path):
    """Rows verified by a PASS in the conformance run (report/out/results.json), per coverage-map SDK key."""
    if not path.exists():
        return {}
    res = json.loads(path.read_text())
    out = {}
    for s in res["scenarios"]:
        for c in s["cases"]:
            for lane, cell in c["cells"].items():
                sdk = LIVE_KEYS[res["lanes"][lane]["sdk"]]
                name = s["title"] if c["id"] == "main" else f"{s['title']}: {c['title']}"
                for r in cell.get("verifies", []):
                    seen = out.setdefault(sdk, {}).setdefault(r, [])
                    if f"{s['id']} {name}" not in seen:
                        seen.append(f"{s['id']} {name}")
    run = f"run {res['run']} UTC, Canton {res['env'].get('canton')}"
    return {k: {r: f"Verified on LocalNet ({run}): " + ", ".join(v) for r, v in rows.items()} for k, rows in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "coverage" / "coverage.json")
    a = ap.parse_args()

    caps = load(ROOT / "standard" / "capabilities.yaml")
    pats = load(HERE / "patterns.yaml")
    sdks = load(HERE / "sdks.yaml")
    overrides = load(HERE / "overrides.yaml") or {} if (HERE / "overrides.yaml").exists() else {}
    rust_alias = {c["id"]: c.get("rust_id", c["id"]) for c in caps}
    live = live_verified(ROOT / "report" / "out" / "results.json")

    result = {"standard_rows": len(caps), "sdks": []}
    for sdk in sdks:
        base = a.repos / sdk["repo"].split("/")[1]
        if not base.exists():
            sys.exit(f"missing checkout: {base}")
        head = subprocess.run(["git", "-C", str(base), "log", "-1", "--format=%h %cs"],
                              capture_output=True, text=True).stdout.strip()
        files = list(source_files(base, sdk))
        texts = {}
        for rel in files:
            try:
                texts[rel] = (base / rel).read_text(errors="ignore")
            except OSError:
                pass
        claims = rust_claims(base, sdk["claims"]) if sdk.get("claims") else set()
        cells = {}
        for c in caps:
            cid, pat = c["id"], pats[c["id"]]
            cell = {"status": "unassessed", "evidence": [], "hits": 0}
            if pat != "MANUAL":
                rx = re.compile(pat, re.I)
                hits = [(rel, len(rx.findall(t))) for rel, t in texts.items()]
                hits = sorted([h for h in hits if h[1]], key=lambda h: (-h[1], h[0]))
                cell["hits"] = sum(n for _, n in hits)
                cell["evidence"] = [f"{rel} ({n})" for rel, n in hits[:3]]
                cell["status"] = "found" if hits else "not_found"
            if rust_alias[cid] in claims:
                cell["status"] = "claimed"
            ov = (overrides.get(sdk["key"]) or {}).get(cid)
            if ov:
                cell["status"] = ov["status"]
                cell["review"] = ov.get("note", "")
            lv = live.get(sdk["key"], {}).get(cid)
            if lv:
                cell["status"] = "verified"
                cell["review"] = (cell.get("review", "") + " | " if cell.get("review") else "") + lv
            cells[cid] = cell
        result["sdks"].append({"key": sdk["key"], "name": sdk["name"], "repo": sdk["repo"], "head": head,
                               "files_scanned": len(texts), "note": sdk.get("note", ""), "cells": cells})
        n = {s: sum(1 for v in cells.values() if v["status"] == s)
             for s in ("claimed", "verified", "found", "partial", "not_found", "not_provided", "unassessed")}
        print(f"{sdk['key']:16} {head:22} files={len(texts):4} {n}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
