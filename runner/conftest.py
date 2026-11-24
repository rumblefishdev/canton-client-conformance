import sys

import pytest

from runner.driver import LANES, ROOT
from runner.session import Session, load_scenarios

SCENARIOS = load_scenarios()


def pytest_addoption(parser):
    g = parser.getgroup("conformance")
    g.addoption("--lanes", default=None, help=f"comma-separated lanes to run (default: all of {', '.join(LANES)})")
    g.addoption("--public", action="store_true", help="leave private scenarios and cases out of the HTML report")


def pytest_configure(config):
    lanes = config.getoption("--lanes")
    config._conformance = Session(lanes.split(",") if lanes else None)


def pytest_generate_tests(metafunc):
    if "cell_id" not in metafunc.fixturenames:
        return
    only = metafunc.config._conformance.lanes
    params = []
    for s in SCENARIOS:
        for c in s["cases"]:
            for lane in s["lanes"]:
                if only is None or lane in only:
                    params.append(pytest.param((s, c, lane), id=f"{s['id']}-{c['id']}-{lane}"))
    metafunc.parametrize("cell_id", params)


@pytest.fixture(scope="session")
def session(request):
    return request.config._conformance


def pytest_sessionfinish(session, exitstatus):
    s = session.config._conformance
    s.close()
    if not s.results:
        return
    data = s.write(SCENARIOS)
    sys.path.insert(0, str(ROOT))
    from report.render import render
    out = ROOT / "report/out/index.html"
    out.write_text(render(data, public=session.config.getoption("--public")))
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr:
        tr.write_line(f"report: {out}  (results: report/out/results.json, transcripts: {s.out})")
