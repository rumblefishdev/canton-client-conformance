"""One test per scenario x case x lane.

The test passes when the SDK behaves as the suite expects: PASS, or a known divergence recorded in the
scenario file (reported as xfail). It fails on anything unexpected: a new failure, or a known divergence
that no longer reproduces (then the scenario file needs updating, and the finding may be fixed upstream).
"""
import os

import pytest

from runner.driver import ROOT
from runner.session import NA, PASS, UNSUPPORTED, expected


def missing(needs):
    if "localnet" in needs and not os.environ.get("LOCALNET_DIR"):
        return "needs a running canton-localnet: set LOCALNET_DIR to its checkout"
    if "vectors" in needs and not (ROOT / "vectors").is_dir():
        return "needs DA's hash vectors: run tools/fetch-vectors.sh"
    return None


def test_cell(session, cell_id):
    scenario, case, lane = cell_id
    why = missing(scenario.get("needs", []))
    if why:
        pytest.skip(why)
    lanes = [l for l in scenario["lanes"] if session.lanes is None or l in session.lanes]
    cell = session.cells(scenario, lanes)[(case["id"], lane)]
    want, finding = expected(case, lane)
    ok = cell.verdict in (want, NA)
    session.record(scenario, case, lane, cell, "expected" if ok else "unexpected")
    if cell.verdict == NA:
        pytest.skip(cell.evidence)
    if not ok:
        hint = f" (known divergence: {finding})" if finding else ""
        pytest.fail(f"expected {want}{hint}, got {cell.verdict}: {cell.evidence}", pytrace=False)
    if want == UNSUPPORTED:
        pytest.skip(f"unsupported: {cell.evidence}")
    if want != PASS:
        pytest.xfail(f"known {want}: {finding}")
