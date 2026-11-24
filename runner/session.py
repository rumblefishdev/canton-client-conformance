"""Scenario model and test session.

A scenario is `scenarios/<id>.yaml` (metadata, cases, Standard rows, known divergences) plus a step
function named in its `impl:` field. The step function runs every case for every lane in one go
(cases often share expensive setup) and returns {(case_id, lane): Cell}.
"""
import importlib, json, os, traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from runner.driver import DAR, LANES, ROOT, Adapter, localnet_config, localnet_version

PASS, FAIL, DIVERGES, UNSUPPORTED, NA, ERROR = "pass", "fail", "diverges", "unsupported", "n/a", "error"
VERDICTS = (PASS, FAIL, DIVERGES, UNSUPPORTED, NA, ERROR)


@dataclass
class Cell:
    verdict: str
    evidence: str
    data: dict = field(default_factory=dict)


def load_scenarios():
    out = []
    for p in sorted((ROOT / "scenarios").glob("*.yaml")):
        s = yaml.safe_load(p.read_text())
        s["file"] = str(p.relative_to(ROOT))
        s.setdefault("cases", [{"id": "main", "title": s["title"]}])
        s.setdefault("private", False)
        for c in s["cases"]:
            c.setdefault("private", s["private"])
            c.setdefault("known", {})
        out.append(s)
    return out


def expected(case, lane):
    """(verdict, finding) the suite expects for this lane; pass unless a known divergence is recorded."""
    k = case["known"].get(lane)
    if k is None:
        return PASS, None
    return k["verdict"], k.get("finding")


def verifies(scenario, case, lane):
    """Ledger Client Standard rows a PASS of this cell verifies (keyed by SDK, not lane)."""
    sdk = LANES[lane]["sdk"]
    return (case.get("verifies") or scenario.get("verifies") or {}).get(sdk, [])


class Session:
    def __init__(self, lanes=None):
        self.started = datetime.now(timezone.utc)
        self.run = self.started.strftime("%Y%m%d-%H%M%S")
        self.out = ROOT / "report/out/runs" / self.run
        self.lanes = lanes
        self.results = {}  # (scenario_id, case_id, lane) -> record
        self.env = {"localnet": localnet_version(), "sdks": {}}
        self._cells = {}
        self._cfg = self._ref = None
        self._open = []

    # ---- shared fixtures ----
    @property
    def localnet(self):
        return bool(os.environ.get("LOCALNET_DIR"))

    @property
    def cfg(self):
        if self._cfg is None:
            self._cfg = localnet_config()
        return self._cfg

    @property
    def ref(self):
        """Reference adapter (raw JSON Ledger API): the oracle. The test DAR is uploaded once."""
        if self._ref is None:
            self._ref = Adapter("reference", self.out / "reference.jsonl")
            self._ref.ok("init", config=self.cfg)
            self._ref.ok("upload_dar", path=str(DAR))
            self.env["canton"] = self._ref.ok("version")["version"]
        return self._ref

    def party(self, hint):
        p = self.ref.ok("allocate_party", hint=f"{hint}-{self.run}", userId=self.cfg["userId"])["party"]
        if "synchronizerId" not in self.cfg:
            self.cfg["synchronizerId"] = self.ref.ok("synchronizers", party=p)["connectedSynchronizers"][0]["synchronizerId"]
        return p

    def adapter(self, lane, log, config=None, token=None, init=True):
        """A fresh adapter process for `lane`, initialised against LocalNet unless `config` says otherwise."""
        a = Adapter(LANES[lane]["adapter"], self.out / log)
        self._open.append(a)
        if init:
            cfg = config if config is not None else {**self.cfg, **LANES[lane]["init"]}
            r = a("init", config=cfg, **({"token": token} if token else {}))
            if r.get("ok"):
                self.env["sdks"].setdefault(LANES[lane]["sdk"], r["result"].get("sdk"))
            a.init_result = r
        return a

    # ---- running ----
    def cells(self, scenario, lanes):
        sid = scenario["id"]
        if sid not in self._cells:
            mod, fn = scenario["impl"].split(":")
            step = getattr(importlib.import_module(f"runner.steps.{mod}"), fn)
            try:
                self._cells[sid] = step(self, scenario, lanes)
            except Exception as e:  # noqa: BLE001  a harness bug: every cell of the scenario reports it
                tb = traceback.format_exc(limit=3)
                self._cells[sid] = {(c["id"], l): Cell(ERROR, f"harness: {type(e).__name__}: {e}", {"traceback": tb})
                                    for c in scenario["cases"] for l in lanes}
        return self._cells[sid]

    def record(self, scenario, case, lane, cell, status):
        exp, finding = expected(case, lane)
        self.results[(scenario["id"], case["id"], lane)] = {
            "verdict": cell.verdict, "evidence": cell.evidence, "data": cell.data, "expected": exp,
            "finding": finding, "status": status,
            "verifies": verifies(scenario, case, lane) if cell.verdict == PASS else []}

    def close(self):
        for a in self._open + ([self._ref] if self._ref else []):
            if not a.p.stdin.closed:
                a.close()

    def to_json(self, scenarios):
        out = {"run": self.run, "started": self.started.isoformat(timespec="seconds"), "env": self.env,
               "lanes": {k: {"label": v["label"], "sdk": v["sdk"]} for k, v in LANES.items()}, "scenarios": []}
        for s in scenarios:
            cases = []
            for c in s["cases"]:
                cells = {l: r for (sid, cid, l), r in self.results.items() if sid == s["id"] and cid == c["id"]}
                if cells:
                    cases.append({k: c.get(k) for k in ("id", "title", "description", "private")} | {"cells": cells})
            if cases:
                out["scenarios"].append({k: s.get(k) for k in (
                    "id", "title", "summary", "standard", "needs", "private", "findings", "file")} | {
                    "lanes": [l for l in s["lanes"] if any(l in c["cells"] for c in cases)], "cases": cases})
        return out

    def write(self, scenarios):
        data = self.to_json(scenarios)
        self.out.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=1, default=str)
        (self.out / "results.json").write_text(text)
        (ROOT / "report/out/results.json").write_text(text)
        return data
