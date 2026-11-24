# canton-sdk-conformance

A cross-SDK conformance suite for Canton Ledger API client libraries. One set of scenarios runs against each SDK,
every result is compared with a reference adapter (raw JSON Ledger API) or a fixed oracle, and the outcome is an
HTML matrix tagged with Ledger Client Standard rows. POC subjects: DA's TypeScript wallet SDK and Nodejumper's
Rust SDK. Status: prototype, groundwork for a Canton Development Fund proposal (RFP 17/18).

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd adapters/ts-wallet-sdk && npm ci)                    # Node >= 22
(cd adapters/rust-nodejumper && cargo build --release)
tools/fetch-vectors.sh                                   # DA hash vectors, pinned commit
# LocalNet scenarios: start canton-localnet as in localnet/README.md, then
LOCALNET_DIR=<canton-localnet checkout> .venv/bin/pytest # about 1 minute
open report/out/index.html
```

Without `LOCALNET_DIR` only the offline scenarios (S11a, S11c) run. Select with `-k S14`, `--lanes ts,rust`.

The exit code is 0 when every cell is as the scenario file expects: a pass, or a known divergence recorded with its
finding (shown as xfail). A new failure, or a known divergence that stops reproducing, fails the run.

## Scenarios

| | Scenario | Lanes |
|---|---|---|
| S1 S2 S4 S5 S6 S7 | connect, parties, create + ACS, exercise, 21-field value round-trip, error taxonomy | TS, Rust |
| S8 | de-duplication and retries under injected faults (`runner/faults.py`) | TS, Rust gRPC/JSON, with and without `RetryConfig` |
| S11a | prepared-transaction hash vs DA's 128 vectors | TS, Rust (offline) |
| S11c | deadline bound by the signature under V2 (documented V2 limitation) | TS, Rust (offline) |
| S14 | ACS reads return every contract, at 5 and 250 contracts | TS, Rust |

Verdicts: ✅ pass · ❌ fail (wrong result, e.g. data missing without an error) · ⚠️ diverges (correct but different:
a loud error where others succeed, or a policy difference to triage) · ➖ unsupported (valid, not a failure).
Findings behind the known divergences: [docs/findings-live.md](docs/findings-live.md),
[docs/findings-hashcheck.md](docs/findings-hashcheck.md).

## Layout

- `scenarios/<id>.yaml`: title, summary, lanes, Standard rows, cases, known divergences per lane, `private`, and
  `impl` (the step function in `runner/steps/`). PASS cells record the rows they verify (`verifies:`), which
  `tools/coverage/scan.py` turns into *Verified* cells on the coverage map.
- `runner/`: pytest plugin and steps. `report/render.py`: results → HTML. Raw transcripts: `report/out/runs/<run>/`.
- `adapters/<sdk>/`: one executable per SDK. **Driver protocol:** one JSON request per stdin line, one response per
  stdout line, `{"ok": true, "result": …}` or `{"ok": false, "error": {status, errorId, message}}`. Ops: `init`,
  `version`, `ledger_end`, `allocate_party`, `list_parties`, `create`, `exercise`, `acs` (`mode`: default, complete,
  paged; optional `interface`), `submit` (caller-chosen `commandId`), `hash`; reference only:
  `create_many`, `upload_dar`, `synchronizers`. Values go in as a tagged neutral form and come back in the JSON
  Ledger API v2 encoding. An SDK without an op answers `{"unsupported": true}`.
- `daml/richtypes/`: test DAR, Peaceful Studio's Apache-2.0 corpus (see `NOTICE`), so no Daml toolchain is needed.

Adding an SDK: write an adapter, add it to `ADAPTERS` and `LANES` in `runner/driver.py`, and list the lane in the
scenario files. The steps do not change.

Credentials: the runner reads the slot-a validator client from the LocalNet checkout at runtime (public demo
credentials); they are redacted from transcripts and never written to the repo.
