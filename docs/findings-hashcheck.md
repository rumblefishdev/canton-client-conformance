# Hash cross-check findings (2026-11-19)

> Section 2A is a documented V2 limitation, raised with the maintainers as
> [nodejumper-org/canton-rust-sdk#26](https://github.com/nodejumper-org/canton-rust-sdk/issues/26).

Setup: DA hash vectors at `canton-network/wallet@8b28459` (128 files). Subjects: `@canton-network/core-tx-hashing`
1.2.0 and `nodejumper-org/canton-rust-sdk@8d8a92b` (`canton_ledger::hashing`). Repro: scenario S11a, `.venv/bin/pytest -k S11a` (offline, after `tools/fetch-vectors.sh`).

| Group | What | TypeScript (DA) | Rust (Nodejumper) |
|---|---|---|---|
| v2 | Canton 3.5.10 prepare responses, scheme V2 | 24/24 | 24/24 |
| v3 | same, scheme V3 | 31/31 | 31/31 |
| held-out | V3 captures made after the TS package was written | 12/12 | 12/12 |
| mutation | one hashed byte changed | 16/16 | 16/16 |
| reject | input that must be refused | 45/45 | **29/42** (3 n/a) |

## 1. Hashes agree: 83/83
For every valid transaction, including contract keys under V3, rollbacks, interfaces, disclosed contracts, all
value types and Canton captures newer than the TS code, the two SDKs compute the same hash as the participant.
This is the core property of external signing, and it holds across two independent implementations.

## 2. Rust hashes 13 inputs the TS package refuses
In each case the Rust SDK returns a hash and the TS package refuses. These are **divergences to triage with
the maintainers, not confirmed bugs**: the reject vectors encode the TS package's policy, which is stricter
than what the Canton spec requires.

**A. Data in the transaction that the signature does not cover (5).** These matter most. A wallet showing the
transaction to the user would show data that the hash does not bind.

| Vector | What is set |
|---|---|
| `reject-v2-max-record-time` | `maxRecordTime` under V2, which V2 does not hash |
| `reject-v2-seed-on-rollback` | a node seed on a rollback node; never hashed |
| `reject-v3-seed-on-fetch` | a node seed on a fetch node; same hash as without it |
| `reject-v2-unknown-field-in-node` | an unknown proto field in a Create node; hash identical to `v2-create-plain` |
| `reject-v3-unknown-field-in-metadata` | an unknown proto field in the metadata |

The two unknown-field cases are lower risk in Rust: `prost` drops unknown fields when decoding, so a Rust
wallet cannot display them either.

`maxRecordTime` under V2 is the clearest case, and it is a **documented protocol limitation, not an SDK bug**. The V2
hash leaves the field out by design: V3 (Canton 3.5.1) was added to include it, and Canton's external-party docs say the
executing participant "can ignore or modify the `max_record_time` field". Canton's encoder copies the field into the
prepared transaction under either scheme. Rust computes the participant's own hash, so its `Prepared` check passes. The
SDK-level point: it defaults to V2 while offering `with_max_record_time()`, whose docs promise the deadline is
enforced. Suggested: document it, refuse or warn under V2, or default to V3 on PV35+. Raised as
[#26](https://github.com/nodejumper-org/canton-rust-sdk/issues/26). The vector is a V3 capture relabelled V2.

**B. Malformed transaction structure accepted (8).** `orphan-node`, `shared-child`, `root-listed-twice`,
`no-roots`, `rollback-without-children`, `two-seeds-for-one-node` (one seed silently wins), `node-id-leading-zero`,
`query-result-in-upper-case`. An honest participant never sends these. Refusing them is defence in depth
against a hostile one, which is the threat model the Rust module documents ("the hash is not taken from the
response, it is recomputed").

**n/a (3):** `too-many-nodes`, `nodes-too-deep`, `value-too-deep` set the TS API's configurable `limits`.
Rust has a fixed depth limit of 256 and no option to configure it.

## Open question
DA's reject vectors encode their package's input policy, which is stricter than the Canton spec. Should they be
normative for every SDK? If so, they belong in the Ledger Client Standard as test data, which is what this project
proposes.
