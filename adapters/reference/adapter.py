#!/usr/bin/env python3
"""Reference adapter: plain HTTP against the JSON Ledger API v2, no SDK. The baseline the SDKs are compared to.
Driver protocol: one JSON request per stdin line, one JSON response per stdout line."""
import json, sys, urllib.request, urllib.error, urllib.parse

GRPC = {0: "Ok", 1: "Cancelled", 2: "Unknown", 3: "InvalidArgument", 4: "DeadlineExceeded", 5: "NotFound",
        6: "AlreadyExists", 7: "PermissionDenied", 8: "ResourceExhausted", 9: "FailedPrecondition", 10: "Aborted",
        11: "OutOfRange", 12: "Unimplemented", 13: "Internal", 14: "Unavailable", 15: "DataLoss", 16: "Unauthenticated"}

class ApiError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body

class Ref:
    def init(self, c, token=None):
        self.c, self.static = c, token
        self._token = token
        return {"sdk": "reference (raw JSON Ledger API v2)", "transport": "json"}

    def token(self):
        if self._token:
            return self._token
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.c["clientId"],
                                       "client_secret": self.c["clientSecret"], "audience": self.c["audience"]}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.c["tokenUrl"], data=data)) as r:
            self._token = json.load(r)["access_token"]
        return self._token

    def http(self, method, path, body=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        rq = urllib.request.Request(self.c["jsonUrl"] + path, data=data, method=method,
                                    headers={"Authorization": f"Bearer {self.token()}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(rq) as r:
                txt = r.read()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            try:
                b = json.loads(txt)
            except ValueError:
                b = {"text": txt}
            raise ApiError(e.code, b)

    # ---- ops ----
    def version(self, r): return {"version": self.http("GET", "/v2/version")["version"]}
    def ledger_end(self, r): return {"offset": self.http("GET", "/v2/state/ledger-end")["offset"]}
    def allocate_party(self, r):
        body = {"partyIdHint": r["hint"], "identityProviderId": ""}
        if r.get("userId"):
            body["userId"] = r["userId"]
        return {"party": self.http("POST", "/v2/parties", body)["partyDetails"]["party"]}
    def list_parties(self, r): return {"parties": [p["party"] for p in self.http("GET", "/v2/parties")["partyDetails"]]}
    def upload_dar(self, r):
        self.http("POST", "/v2/packages", raw=open(r["path"], "rb").read(), ctype="application/octet-stream")
        return {}
    def synchronizers(self, r):
        return self.http("GET", "/v2/state/connected-synchronizers?party=" + urllib.parse.quote(r["party"]))
    def _submit(self, cmd, act_as, command_id=None):
        import uuid
        cmds = cmd if isinstance(cmd, list) else [cmd]
        res = self.http("POST", "/v2/commands/submit-and-wait-for-transaction",
                        {"commands": {"commands": cmds, "commandId": command_id or str(uuid.uuid4()), "actAs": [act_as]}})
        return res["transaction"]
    def create_many(self, r):
        cmd = {"CreateCommand": {"templateId": tid(r["template"]), "createArguments": lf_record(r["args"])}}
        left = r["count"]
        while left:
            n = min(left, 50)
            self._submit([cmd] * n, r["actAs"])
            left -= n
        return {"created": r["count"]}
    def submit(self, r):
        """create with a caller-chosen command id; {"duplicate": true} when the ledger de-duplicates it."""
        try:
            tx = self._submit({"CreateCommand": {"templateId": tid(r["template"]), "createArguments": lf_record(r["args"])}},
                              r["actAs"], r.get("commandId"))
        except ApiError as e:
            if isinstance(e.body, dict) and e.body.get("code") == "DUPLICATE_COMMAND":
                return {"duplicate": True, "errorId": "DUPLICATE_COMMAND"}
            raise
        return {"updateId": tx["updateId"]}
    def create(self, r):
        tx = self._submit({"CreateCommand": {"templateId": tid(r["template"]), "createArguments": lf_record(r["args"])}}, r["actAs"])
        cid = next((e["CreatedEvent"]["contractId"] for e in tx["events"] if "CreatedEvent" in e), None)
        return {"contractId": cid, "updateId": tx["updateId"]}
    def exercise(self, r):
        tx = self._submit({"ExerciseCommand": {"templateId": tid(r["template"]), "contractId": r["contractId"],
                                               "choice": r["choice"], "choiceArgument": lf(r["arg"])}}, r["actAs"])
        return {"updateId": tx["updateId"]}
    def acs(self, r):
        """mode "plain" (default): one POST /v2/state/active-contracts; "paged": every page of /v2/state/active-contracts-page."""
        end = self.http("GET", "/v2/state/ledger-end")["offset"]
        body = {"activeAtOffset": end, "eventFormat": {"verbose": True, "filtersByParty": {r["party"]: {"cumulative": [
            {"identifierFilter": {"TemplateFilter": {"value": {"templateId": tid(r["template"]), "includeCreatedEventBlob": False}}}}]}}}}
        if r.get("mode") == "paged":
            rows, token, pages = [], None, 0
            while True:
                page = self.http("POST", "/v2/state/active-contracts-page", {**body, **({"pageToken": token} if token else {})})
                rows += page.get("activeContracts", [])
                pages += 1
                token = page.get("nextPageToken")
                if not token:
                    break
        else:
            rows = self.http("POST", "/v2/state/active-contracts", body)
        out = []
        for row in rows:
            ev = (row.get("contractEntry") or {}).get("JsActiveContract", {}).get("createdEvent")
            if ev:
                out.append({"contractId": ev["contractId"], "args": ev["createArgument"]})
        return {"contracts": out}

def tid(t): return f'{t["package"]}:{t["module"]}:{t["entity"]}'

def lf(v):
    (tag, x), = v.items()
    if tag in ("party", "text", "int64", "numeric", "date", "timestamp", "contractId", "bool", "enum"): return x
    if tag == "unit": return {}
    if tag == "optional":  # JSON Ledger API v2: a nested Optional chain is one array level per Optional
        if x is None: return None
        return arr(v) if "optional" in x else lf(x)
    if tag == "list": return [lf(w) for w in x]
    if tag == "textMap": return {k: lf(w) for k, w in x.items()}
    if tag == "genMap": return [[lf(k), lf(w)] for k, w in x]
    if tag == "record": return lf_record(x)
    if tag == "variant": return {"tag": x["constructor"], "value": lf(x["value"])}
    raise ValueError(tag)

def arr(v):
    x = v["optional"]
    if x is None: return []
    return [arr(x)] if "optional" in x else [lf(x)]

def lf_record(fields): return {k: lf(w) for k, w in fields}

def main():
    ref = Ref()
    for line in sys.stdin:
        req = json.loads(line)
        try:
            if req["op"] == "init":
                res = ref.init(req["config"], req.get("token"))
            else:
                res = getattr(ref, req["op"])(req)
            out = {"id": req["id"], "ok": True, "result": res}
        except ApiError as e:
            b = e.body if isinstance(e.body, dict) else {}
            out = {"id": req["id"], "ok": False, "error": {
                "status": GRPC.get(b.get("grpcCodeValue")) or f"HTTP {e.status}", "errorId": b.get("code"),
                "message": str(b.get("cause") or b)[:600], "http": e.status}}
        except Exception as e:  # noqa: BLE001
            out = {"id": req["id"], "ok": False, "error": {"message": f"{type(e).__name__}: {e}"}}
        print(json.dumps(out), flush=True)

if __name__ == "__main__":
    main()
