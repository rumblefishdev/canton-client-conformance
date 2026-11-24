"""S8: de-duplication and retries when a submission fails or its outcome is lost.

SDK traffic goes through runner/faults.py; the reference talks to the participant directly. Every case
runs on a fresh party, so the oracle is how many Asset contracts the reference finds afterwards.

  control-no-id      two submits without a command id: an app retry with a new id commits twice
  same-id-sequential the same command id twice, in turn: the second must be DUPLICATE_COMMAND
  same-id-concurrent the same command id twice at once: the participant answers SUBMISSION_ALREADY_IN_FLIGHT
  backpressure       SEQUENCER_BACKPRESSURE before execution (fault proxy, JSON lanes only)
  lost-response      the command commits, the response is lost; the app resubmits with the same id

A lane that retries by itself (TS always, Rust with RetryConfig) must retry a retriable error; a lane that
does not must surface it with its error id. No lane may ever commit a command id twice.
"""
import threading, time

from runner.driver import LANES, T, outcome
from runner.faults import HttpProxy, TcpProxy
from runner.session import FAIL, NA, PASS, Cell


def args(p):
    return [["issuer", {"party": p}], ["amount", {"numeric": "1"}]]


class Run:
    def __init__(self, session):
        self.s = session
        self.http = HttpProxy(session.cfg["jsonUrl"])
        self.tcp = TcpProxy(int(session.cfg["grpcEndpoint"].rsplit(":", 1)[1]))
        self.n = 0

    def party(self):
        self.n += 1
        return self.s.party(f"dd{self.n}")

    def adapter(self, lane):
        cfg = {**self.s.cfg, **LANES[lane]["init"], "jsonUrl": self.http.url, "grpcEndpoint": self.tcp.endpoint}
        return self.s.adapter(lane, f"dedup-{lane}.jsonl", config=cfg)

    def contracts(self, p):
        return len(self.s.ref.ok("acs", party=p, template=T("Asset"))["contracts"])

    def attempts(self, cid):
        return len([x for x in self.http.attempts if x["commandId"] == cid])

    def close(self):
        self.http.close()
        self.tcp.close()


def submit(a, p, cid=None):
    return a("submit", template=T("Asset"), args=args(p), actAs=p, **({"commandId": cid} if cid else {}))


def json_lane(lane):
    return LANES[lane]["transport"] == "json"


def control_no_id(r, lane):
    p, a = r.party(), r.adapter(lane)
    res = [outcome(submit(a, p)) for _ in range(2)]
    a.close()
    n = r.contracts(p)
    return Cell(PASS if n == 2 else FAIL, f"app sees {' / '.join(res)}; ledger holds {n}", {"ledger": n})


def same_id_sequential(r, lane):
    p, a, cid = r.party(), r.adapter(lane), f"a1-{r.s.run}-{r.n}"
    res = [outcome(submit(a, p, cid)) for _ in range(2)]
    a.close()
    n = r.contracts(p)
    ok = n == 1 and res[0] == "ok" and res[1].endswith("DUPLICATE_COMMAND")
    return Cell(PASS if ok else FAIL, f"app sees {' / '.join(res)}; ledger holds {n}", {"ledger": n})


def same_id_concurrent(r, lane):
    p, cid = r.party(), f"a2-{r.s.run}-{r.n}"
    adapters = [r.adapter(lane) for _ in range(2)]
    r.http.arm(None)
    res = [None, None]

    def go(i):
        res[i] = outcome(submit(adapters[i], p, cid))
    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    t = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    secs = round(time.time() - t, 1)
    for a in adapters:
        a.close()
    n, res = r.contracts(p), sorted(res)
    tries = r.attempts(cid) if json_lane(lane) else None
    ev = f"app sees {' / '.join(res)}; ledger holds {n}; {secs}s" + (f"; {tries} attempts for 2 calls" if tries else "")
    if n != 1 or "ok" not in res:
        return Cell(FAIL, ev, {"ledger": n})
    other = res[0] if res[1] == "ok" else res[1]
    if LANES[lane]["retries"] and other == "SUBMISSION_ALREADY_IN_FLIGHT":
        return Cell(FAIL, ev + " (a retriable error reached the app although the SDK retries)", {"ledger": n})
    return Cell(PASS if other in ("DUPLICATE_COMMAND", "SUBMISSION_ALREADY_IN_FLIGHT") else FAIL, ev, {"ledger": n})


def backpressure(r, lane):
    if not json_lane(lane):
        return Cell(NA, "the fault proxy injects this error on the JSON API only")
    p, a, cid = r.party(), r.adapter(lane), f"b1-{r.s.run}-{r.n}"
    r.http.arm("reject-before")
    res = outcome(submit(a, p, cid))
    a.close()
    n, tries = r.contracts(p), r.attempts(cid)
    ev = f"app sees {res}; ledger holds {n}; {tries} attempt(s)"
    if LANES[lane]["retries"]:
        ok = res == "ok" and n == 1 and tries >= 2
        return Cell(PASS if ok else FAIL, ev + ("" if ok else " (the SDK retries, but not this retriable error)"),
                    {"ledger": n, "attempts": tries})
    ok = res == "SEQUENCER_BACKPRESSURE" and n == 0
    return Cell(PASS if ok else FAIL, ev, {"ledger": n, "attempts": tries})


def lost_response(r, lane):
    p, a, cid = r.party(), r.adapter(lane), f"b2-{r.s.run}-{r.n}"
    if json_lane(lane):
        r.http.arm("lose-after")
    else:
        r.tcp.arm()
    first = outcome(submit(a, p, cid))
    tries = r.attempts(cid) if json_lane(lane) else r.tcp.connections
    committed = r.contracts(p)
    again = outcome(submit(a, p, cid))
    a.close()
    after = r.contracts(p)
    ev = (f"app sees {first} for a command the ledger committed {committed}x ({tries} attempt(s)); "
          f"resubmit with the same id: {again}; ledger holds {after}")
    ok = committed == 1 and after == 1 and again.endswith("DUPLICATE_COMMAND")
    return Cell(PASS if ok else FAIL, ev, {"first": first, "resubmit": again, "ledger": after})


CASES = {"control-no-id": control_no_id, "same-id-sequential": same_id_sequential,
         "same-id-concurrent": same_id_concurrent, "backpressure": backpressure, "lost-response": lost_response}


def run(session, scenario, lanes):
    r = Run(session)
    try:
        return {(case, lane): fn(r, lane) for case, fn in CASES.items() for lane in lanes}
    finally:
        r.close()
