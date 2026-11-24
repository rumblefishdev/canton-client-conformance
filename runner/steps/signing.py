"""S11: interactive submission, offline. What does each SDK compute and sign for a prepared transaction?

hash_vectors     every DA hash vector through the SDK's hasher, one case per vector group
max_record_time  one transaction with three deadlines (scenarios/fixtures/max-record-time)
"""
import json

from runner.driver import ROOT
from runner.session import DIVERGES, FAIL, NA, PASS, Cell

VECTORS = ROOT / "vectors"
FIXTURES = ROOT / "scenarios/fixtures/max-record-time"


def hasher(session, lane):
    return session.adapter(lane, f"hash-{lane}.jsonl", init=False)


def hash_one(a, v):
    r = a("hash", preparedTransaction=v["preparedTransaction"], scheme=v["scheme"],
          **({"limits": v["limits"]} if v.get("limits") else {}))
    res = r.get("result") or {}
    if res.get("unsupported"):
        return {"na": res.get("reason")}
    if r.get("ok"):
        return {"hash": res["hash"]}
    return {"error": (r.get("error") or {}).get("message", "error")}


# ---- hash vectors ----
def hash_vectors(session, scenario, lanes):
    vecs = {}
    for p in sorted(VECTORS.rglob("*.json")):
        v = json.loads(p.read_text())
        vecs.setdefault(p.parent.name, []).append(v)
    out = {}
    for lane in lanes:
        a = hasher(session, lane)
        for case in [c["id"] for c in scenario["cases"]]:
            ok, bad, na = 0, [], 0
            for v in vecs.get(case, []):
                r, exp = hash_one(a, v), v["expected"]
                if "na" in r:
                    na += 1
                elif "hash" in exp:
                    if r.get("hash") == exp["hash"]:
                        ok += 1
                    else:
                        bad.append(f"{v['id']}: " + (f"got {r['hash'][:16]}…" if "hash" in r else f"refused ({r['error'][:60]})"))
                elif "error" in r:
                    ok += 1
                else:
                    bad.append(f"{v['id']}: accepted, returned {r['hash'][:16]}… (expected refusal {exp['error']})")
            total = ok + len(bad)
            ev = f"{ok}/{total}" + (f", {na} n/a (vector sets the TS package's configurable limits)" if na else "")
            if not total:
                out[(case, lane)] = Cell(NA, f"no vectors in group {case}")
            elif not bad:
                out[(case, lane)] = Cell(PASS, ev)
            else:
                # reject vectors encode the TS package's policy, stricter than the Canton spec: a divergence to triage
                out[(case, lane)] = Cell(DIVERGES if case == "reject" else FAIL, ev, {"mismatches": bad})
        a.close()
    return out


# ---- max record time ----
def max_record_time(session, scenario, lanes):
    fx = [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]
    out = {}
    for lane in lanes:
        a = hasher(session, lane)
        for case, scheme in (("v2-deadline", "HASHING_SCHEME_VERSION_V2"), ("v3-control", "HASHING_SCHEME_VERSION_V3")):
            res = {f["variant"]: hash_one(a, f) for f in fx if f["scheme"] == scheme}
            ev = "; ".join(f"{k}: " + (r["hash"][:16] + "…" if "hash" in r else "refused") for k, r in res.items())
            hashes = [r["hash"] for r in res.values() if "hash" in r]
            if len(hashes) != len(set(hashes)):
                if case == "v2-deadline":  # correct for V2, which does not sign the deadline: a policy divergence
                    out[(case, lane)] = Cell(DIVERGES, ev + " (same hash for every deadline: correct for V2, which does "
                                             "not sign it; the deadline is not bound by the signature)", {"results": res})
                else:
                    out[(case, lane)] = Cell(FAIL, ev + " (one signature authorises the transaction with different "
                                             "deadlines)", {"results": res})
            elif case == "v3-control" and len(hashes) != 3:
                out[(case, lane)] = Cell(FAIL, ev + " (V3 covers the deadline: expected three hashes)", {"results": res})
            else:
                out[(case, lane)] = Cell(PASS, ev, {"results": res})
        a.close()
    return out
