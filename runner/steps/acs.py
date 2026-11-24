"""S14: does each SDK's ACS read return every active contract?

A fresh party gets Asset contracts (party field `issuer`, implements Holding) and Marker contracts (party
field `owner`), first 5 of each and then 250, above the JSON API's default list limit (200). Oracle: the
reference reading every page of /v2/state/active-contracts-page. Each read uses a fresh SDK instance
except the warm-cache case, which repeats the default read on one long-lived instance.

Verdicts: every contract at every size -> pass; fewer, without an error -> fail (silent data loss);
an error instead of the contracts -> diverges (loud, but other SDKs return them).
"""
import time

from runner.driver import T
from runner.session import DIVERGES, FAIL, PASS, UNSUPPORTED, Cell

SIZES = (5, 250)
READS = {  # case -> (template, request extras)
    "default": ("Asset", {}),
    "complete-issuer": ("Asset", {"mode": "complete"}),
    "complete-owner": ("Marker", {"mode": "complete"}),
    "paged": ("Asset", {"mode": "paged"}),
    "interface": ("Asset", {"mode": "complete", "interface": T("Holding")}),
}


def read(a, party, template, extra):
    r = a("acs", party=party, template=T(template), **extra)
    res, err = r.get("result") or {}, r.get("error") or {}
    if res.get("unsupported") or err.get("unsupported"):
        return None, "unsupported: " + (res.get("reason") or err.get("message", ""))
    if r.get("ok"):
        return len(res["contracts"]), None
    return None, err.get("errorId") or err.get("message", "")[:80]


def show(n, err, want):
    return f"ERROR {err}" if err else f"{n}/{want}"


def verdict(rows):
    """rows: [(size, want, n, err)]"""
    ev = " · ".join(f"{size} created: {show(n, err, want)}" for size, want, n, err in rows)
    if any(err and err.startswith("unsupported") for _, _, _, err in rows):
        return Cell(UNSUPPORTED, ev)
    if all(err is None and n == want for _, want, n, err in rows):
        return Cell(PASS, ev)
    if any(err is None and n < want for _, want, n, err in rows):
        return Cell(FAIL, ev + " (contracts missing, no error)")
    return Cell(DIVERGES, ev)


def run(session, scenario, lanes):
    ref = session.ref
    P = session.party("acs")
    args = {"Asset": [["issuer", {"party": P}], ["amount", {"numeric": "1"}]], "Marker": [["owner", {"party": P}]]}
    warm = {lane: session.adapter(lane, f"acs-{lane}-warm.jsonl") for lane in lanes}
    got, oracle, have = {}, {}, 0
    for size in SIZES:
        for tpl in args:
            ref.ok("create_many", template=T(tpl), args=args[tpl], actAs=P, count=size - have)
        have = size
        time.sleep(1)
        oracle[size] = {tpl: len(ref.ok("acs", party=P, template=T(tpl), mode="paged")["contracts"]) for tpl in args}
        for case, (tpl, extra) in READS.items():
            for lane in lanes:
                a = session.adapter(lane, f"acs-{lane}.jsonl")
                got[(case, lane, size)] = read(a, P, tpl, extra)
                a.close()
        for lane in lanes:
            got[("warm-cache", lane, size)] = read(warm[lane], P, "Asset", {})
    for a in warm.values():
        a.close()

    out = {}
    for lane in lanes:
        for case, (tpl, _) in READS.items():
            out[(case, lane)] = verdict([(s, oracle[s][tpl], *got[(case, lane, s)]) for s in SIZES])
            out[(case, lane)].data = {"oracle": {s: oracle[s][tpl] for s in SIZES},
                                      "got": {s: got[(case, lane, s)] for s in SIZES}}
        fresh = [got[("default", lane, s)] for s in SIZES]
        warmed = [got[("warm-cache", lane, s)] for s in SIZES]
        ev = " · ".join(f"{s} created: warm {show(*w, oracle[s]['Asset'])}, fresh {show(*f, oracle[s]['Asset'])}"
                        for s, w, f in zip(SIZES, warmed, fresh))
        out[("warm-cache", lane)] = Cell(PASS if warmed == fresh else DIVERGES,
                                         ev + ("" if warmed == fresh else " (same call, same ledger, different answer)"))
    return out
