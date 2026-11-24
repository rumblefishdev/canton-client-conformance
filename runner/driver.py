"""Adapter processes, LocalNet config, lanes and value normalisation shared by every scenario.

Every adapter is a separate process speaking the driver protocol: one JSON request per stdin line,
one JSON response per stdout line. Credentials are read at runtime from the LocalNet checkout (public
demo creds), redacted from transcripts and never written to the repo.
"""
import json, os, re, subprocess, time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = {
    "reference": ["python3", str(ROOT / "adapters/reference/adapter.py")],
    "ts": [os.environ.get("NODE", "node"), str(ROOT / "adapters/ts-wallet-sdk/adapter.mjs")],
    "rust": [str(ROOT / "adapters/rust-nodejumper/target/release/rust-nodejumper-adapter")],
}
# A lane is one SDK in one configuration. `retries`: the SDK is set up to retry retriable errors by itself
# (TS always has its built-in retry; Rust only with RetryConfig).
LANES = {
    "ts": {"adapter": "ts", "sdk": "ts", "label": "TypeScript (DA)", "transport": "json", "retries": True, "init": {}},
    "rust": {"adapter": "rust", "sdk": "rust", "label": "Rust (Nodejumper)", "transport": "grpc", "retries": False,
             "init": {}},
    "rust-json": {"adapter": "rust", "sdk": "rust", "label": "Rust, JSON API", "transport": "json", "retries": False,
                  "init": {"transport": "json"}},
    "rust-retry": {"adapter": "rust", "sdk": "rust", "label": "Rust + RetryConfig", "transport": "grpc",
                   "retries": True, "init": {"retry": True}},
    "rust-json-retry": {"adapter": "rust", "sdk": "rust", "label": "Rust, JSON + RetryConfig", "transport": "json",
                        "retries": True, "init": {"transport": "json", "retry": True}},
}
SDKS = {"ts": "TypeScript (DA)", "rust": "Rust (Nodejumper)"}
PKG = "#richtypes-h07285b495f9e"
DAR = ROOT / "daml/richtypes/richtypes.dar"
REDACT = []


def T(entity):
    return {"package": PKG, "module": "RichTypes", "entity": entity}


class Fail(Exception):
    pass


class Unsupported(Exception):
    pass


def localnet_config():
    d = Path(os.environ["LOCALNET_DIR"])
    env = (d / "compose/modules/keycloak/env/a-validator-1/on/oauth2.env").read_text()
    g = lambda k: re.search(rf"^{k}=(\S+)", env, re.M).group(1)
    c = {"jsonUrl": "http://localhost:11975", "grpcEndpoint": "http://localhost:11901",
         "tokenUrl": "http://localhost:8082/realms/AValidator1/protocol/openid-connect/token",
         "oidcConfigUrl": "http://localhost:8082/realms/AValidator1/.well-known/openid-configuration",
         "audience": "https://canton.network.global",
         "clientId": g("AUTH_A_VALIDATOR_1_VALIDATOR_CLIENT_ID"),
         "clientSecret": g("AUTH_A_VALIDATOR_1_VALIDATOR_CLIENT_SECRET"),
         "userId": g("AUTH_A_VALIDATOR_1_VALIDATOR_USER_ID")}
    REDACT.append(c["clientSecret"])
    return c


def localnet_version():
    d = os.environ.get("LOCALNET_DIR")
    if not d:
        return None
    r = subprocess.run(["git", "-C", d, "describe", "--tags", "--always"], capture_output=True, text=True)
    return r.stdout.strip() or None


class Adapter:
    def __init__(self, name, log):
        self.name, self.log, self.n = name, Path(log), 0
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.p = subprocess.Popen(ADAPTERS[name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=open(self.log.with_suffix(".stderr"), "a"), text=True)

    def __call__(self, op, **kw):
        self.n += 1
        req = {"id": self.n, "op": op, **kw}
        t = time.time()
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        resp = json.loads(line) if line else {"ok": False, "error": {"message": "adapter exited"}}
        rec = json.dumps({"req": req, "resp": resp, "ms": round(1000 * (time.time() - t))})
        for s in REDACT:
            rec = rec.replace(s, "<redacted>")
        with open(self.log, "a") as f:
            f.write(rec + "\n")
        return resp

    def ok(self, op, **kw):
        r = self(op, **kw)
        if not r.get("ok"):
            raise Fail(f"{op} failed: {json.dumps(r.get('error'))[:400]}")
        res = r["result"]
        if isinstance(res, dict) and res.get("unsupported"):
            raise Unsupported(f"{op}: {res.get('reason')}")
        return res

    def close(self):
        if not self.p.stdin.closed:
            self.p.stdin.close()
            self.p.wait(timeout=10)


def outcome(r):
    """Short label for a driver response: "ok" or the error id / status."""
    if r.get("ok"):
        return "ok"
    e = r.get("error") or {}
    return e.get("errorId") or e.get("status") or (e.get("message") or "error")[:40]


# ---- normalisation: compare Daml-LF JSON values semantically ----
def norm(v):
    if isinstance(v, str):
        if re.fullmatch(r"-?\d+\.\d+", v):
            return ("num", Decimal(v).normalize())
        if re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", v):
            return ("ts", datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        return v
    if isinstance(v, dict):
        return {k: norm(w) for k, w in v.items()}
    if isinstance(v, list):
        return [norm(w) for w in v]
    return v


def diff(a, b, path="$"):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: {json.dumps(a, default=str)} != {json.dumps(b, default=str)}")
    return out
