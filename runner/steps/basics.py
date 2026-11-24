"""S1-S7: the basic Ledger API operations, each SDK compared with the reference adapter.

Every SDK lane gets one party (allocated by the reference with act-as rights), one Marker and one Asset,
shared by the scenarios. A check is fn(sdk_adapter, ref_adapter, ctx) -> evidence; it raises Fail or Unsupported.
"""
import base64, hashlib, hmac, json, secrets, sys, time

from runner.driver import ROOT, Fail, T, Unsupported, diff, norm
from runner.session import FAIL, PASS, UNSUPPORTED, Cell

_lanes = {}  # lane -> (adapter, ctx), shared by the basic scenarios
_seen = {}   # (party, template) -> contract ids the reference saw before a create


def _lane(session, lane):
    if lane not in _lanes:
        P = session.party(f"conf-{lane}")
        ref = session.ref
        a = session.adapter(lane, f"{lane}.jsonl")
        ctx = {"lane": lane, "run": session.run, "party": P, "session": session,
               "marker": ref.ok("create", template=T("Marker"), args=[["owner", {"party": P}]], actAs=P)["contractId"],
               "asset": ref.ok("create", template=T("Asset"), args=[["issuer", {"party": P}], ["amount", {"numeric": "5"}]],
                               actAs=P)["contractId"]}
        _lanes[lane] = (a, ctx)
    return _lanes[lane]


def per_lane(check):
    def step(session, scenario, lanes):
        out = {}
        for lane in lanes:
            a, ctx = _lane(session, lane)
            if not a.init_result.get("ok"):
                out[("main", lane)] = Cell(FAIL, f"init failed: {a.init_result.get('error')}")
                continue
            try:
                out[("main", lane)] = Cell(PASS, check(a, session.ref, ctx))
            except Unsupported as e:
                out[("main", lane)] = Cell(UNSUPPORTED, str(e))
            except Fail as e:
                out[("main", lane)] = Cell(FAIL, str(e))
        return out
    return step


def find(acs, cid):
    return next((c for c in acs["contracts"] if c["contractId"] == cid), None)


def remember(ref, party, template):
    _seen[(party, json.dumps(template))] = {c["contractId"] for c in ref.ok("acs", party=party, template=template)["contracts"]}


def created_cid(ref, party, template, res):
    """SDKs that do not return the contract id on create: find it via the reference ACS (the only new one)."""
    if res.get("contractId"):
        return res["contractId"], "returned by create"
    new = [c["contractId"] for c in ref.ok("acs", party=party, template=template)["contracts"]
           if c["contractId"] not in _seen.setdefault((party, json.dumps(template)), set())]
    if len(new) != 1:
        raise Fail(f"create returned no contract id and {len(new)} new contracts found")
    return new[0], "not returned by create; found via ACS"


@per_lane
def s1_connect(a, ref, ctx):
    end = a.ok("ledger_end")["offset"]
    if not isinstance(end, int) or end < 0:
        raise Fail(f"ledger_end returned {end!r}")
    try:
        v = a.ok("version")["version"]
    except Unsupported as e:
        return f"ledger end {end}; version: not exposed ({e})"
    if v != ref.ok("version")["version"]:
        raise Fail(f"version {v} != reference")
    return f"Ledger API {v}, ledger end {end}"


@per_lane
def s2_parties(a, ref, ctx):
    hint = f"s2-{ctx['lane']}-{ctx['run']}"
    p = a.ok("allocate_party", hint=hint)["party"]
    if not p.startswith(hint + "::"):
        raise Fail(f"allocated {p!r}, expected {hint}::<fingerprint>")
    seen_sdk = p in a.ok("list_parties")["parties"]
    seen_ref = p in ref.ok("list_parties")["parties"]
    if not seen_ref:
        raise Fail("party not visible to the reference")
    if not seen_sdk:
        raise Fail("SDK's own list_parties does not include the party it allocated")
    return f"allocated and listed {p.split('::')[0]}"


@per_lane
def s4_create_acs(a, ref, ctx):
    P = ctx["party"]
    remember(ref, P, T("Asset"))
    res = a.ok("create", template=T("Asset"), args=[["issuer", {"party": P}], ["amount", {"numeric": "12.5"}]], actAs=P)
    cid, how = created_cid(ref, P, T("Asset"), res)
    mine = find(a.ok("acs", party=P, template=T("Asset")), cid)
    if not mine:
        raise Fail(f"SDK ACS does not contain the contract it just created ({how})")
    theirs = find(ref.ok("acs", party=P, template=T("Asset")), cid)
    d = diff(norm(mine["args"]), norm(theirs["args"]))
    if d:
        raise Fail("ACS payload differs from reference: " + "; ".join(d))
    return f"created + read back via own ACS; contract id {how}; payload matches reference ({mine['args']})"


@per_lane
def s5_exercise(a, ref, ctx):
    P = ctx["party"]
    remember(ref, P, T("Marker"))
    m, _ = created_cid(ref, P, T("Marker"), a.ok("create", template=T("Marker"), args=[["owner", {"party": P}]], actAs=P))
    remember(ref, P, T("Asset"))
    a.ok("exercise", template=T("Marker"), contractId=m, choice="Mint", arg={"record": [["amount", {"numeric": "7.25"}]]}, actAs=P)
    mine = a.ok("acs", party=P, template=T("Asset"))["contracts"]
    new = [c for c in mine if c["contractId"] not in _seen[(P, json.dumps(T("Asset")))]]
    if len(new) != 1:
        raise Fail(f"expected 1 new Asset in SDK ACS after Mint, found {len(new)}")
    if norm(new[0]["args"]["amount"]) != norm("7.25"):
        raise Fail(f"minted amount {new[0]['args']['amount']}")
    if not find(a.ok("acs", party=P, template=T("Marker")), m):
        raise Fail("non-consuming choice archived the Marker")
    return f"Mint (non-consuming) created Asset amount={new[0]['args']['amount']}; Marker still active"


def rich_args(P, marker, asset):
    return [
        ["owner", {"party": P}], ["count", {"int64": "-9007199254740993"}], ["amount", {"numeric": "-1234567890.0123456789"}],
        ["label", {"text": "zażółć 🦀 \"quotes\" \\ \n tab\t"}], ["active", {"bool": False}], ["asOf", {"date": "1969-12-31"}],
        ["observedAt", {"timestamp": "2026-11-19T12:34:56.789012Z"}], ["note", {"optional": None}],
        ["nestedNote", {"optional": {"optional": None}}], ["tags", {"list": [{"text": "a"}, {"text": ""}, {"text": "a"}]}],
        ["attributes", {"textMap": {"z": {"text": "last"}, "a": {"text": "first"}, "": {"text": "empty key"}}}],
        ["genMap", {"genMap": [[{"party": P}, {"int64": "3"}]]}], ["unitField", {"unit": {}}],
        ["marker", {"contractId": marker}], ["holdingCid", {"contractId": asset}],
        ["holdingCids", {"list": [{"contractId": asset}, {"contractId": asset}]}],
        ["profile", {"record": [["nickname", {"text": "n"}], ["level", {"int64": "9223372036854775807"}]]}],
        ["outcome", {"variant": {"constructor": "Win", "value": {"record": [["prize", {"numeric": "0.01"}], ["tier", {"text": "gold"}]]}}}],
        ["suit", {"enum": "Spades"}], ["fee", {"numeric": "99999999999999999999999999999999999.99"}],
    ]


@per_lane
def s6_roundtrip(a, ref, ctx):
    P = ctx["party"]
    args = rich_args(P, ctx["marker"], ctx["asset"])
    remember(ref, P, T("RichRecord"))
    cid, _ = created_cid(ref, P, T("RichRecord"), a.ok("create", template=T("RichRecord"), args=args, actAs=P))
    mine = find(a.ok("acs", party=P, template=T("RichRecord")), cid)
    theirs = find(ref.ok("acs", party=P, template=T("RichRecord")), cid)
    if not mine:
        raise Fail("record not in SDK ACS")
    sys.path.insert(0, str(ROOT / "adapters/reference"))
    from adapter import lf_record
    d_exp = diff(norm(mine["args"]), norm(lf_record(args)))
    d_ref = diff(norm(mine["args"]), norm(theirs["args"]))
    raw = diff(mine["args"], theirs["args"])
    if d_exp or d_ref:
        raise Fail("; ".join(["vs input: " + x for x in d_exp] + ["vs reference: " + x for x in d_ref]))
    note = f"; raw encoding differs from reference in {len(raw)} field(s) but is semantically equal: {raw[:3]}" if raw else "; byte-identical to reference"
    return f"21 fields over every Daml-LF type round-trip semantically{note}"


@per_lane
def s7_errors(a, ref, ctx):
    P = ctx["party"]
    remember(ref, P, T("Asset"))
    cid, _ = created_cid(ref, P, T("Asset"), a.ok("create", template=T("Asset"), args=[["issuer", {"party": P}], ["amount", {"numeric": "1"}]], actAs=P))
    ref.ok("exercise", template=T("Asset"), contractId=cid, choice="Archive", arg={"record": []}, actAs=P)
    got = a("exercise", template=T("Asset"), contractId=cid, choice="Archive", arg={"record": []}, actAs=P)
    want = ref("exercise", template=T("Asset"), contractId=cid, choice="Archive", arg={"record": []}, actAs=P)
    if got.get("ok"):
        raise Fail("exercising an archived contract succeeded")
    e, w = got["error"], want["error"]
    out = [f"archived contract -> status={e.get('status')} errorId={e.get('errorId')} (reference: {w.get('status')}/{w.get('errorId')})"]
    problems = []
    if e.get("errorId") != w.get("errorId"):
        problems.append(f"error id not surfaced as {w.get('errorId')!r} (got {e.get('errorId')!r})")
    if (e.get("status") or "").lower().replace("_", "") != (w.get("status") or "").lower().replace("_", ""):
        problems.append(f"status {e.get('status')!r} != {w.get('status')!r}")
    session = ctx["session"]
    b = session.adapter(ctx["lane"], f"{ctx['lane']}-badtoken.jsonl", token=forged_jwt(session.cfg))
    bad = b("ledger_end") if b.init_result.get("ok") else b.init_result
    b.close()
    if bad.get("ok"):
        problems.append("ledger_end with an invalid token succeeded")
    else:
        be = bad["error"]
        out.append(f"bad token -> status={be.get('status')} errorId={be.get('errorId')}")
        if "unauth" not in json.dumps(be).lower() and "401" not in json.dumps(be):
            problems.append("invalid-token error is not recognisable as UNAUTHENTICATED")
    if problems:
        raise Fail("; ".join(problems) + " | " + " | ".join(out))
    return " | ".join(out)


def forged_jwt(cfg):
    """Well-formed, unexpired JWT signed with a random key: the participant must answer UNAUTHENTICATED."""
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    head = enc({"alg": "HS256", "typ": "JWT"})
    body = enc({"sub": cfg["userId"], "aud": cfg["audience"], "exp": int(time.time()) + 3600, "iat": int(time.time())})
    sig = base64.urlsafe_b64encode(hmac.new(secrets.token_bytes(32), f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{head}.{body}.{sig}"
