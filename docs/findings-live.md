# Live scenario findings (2026-11-19)

Setup: canton-localnet v0.10.2-1 (Splice 0.10.2, **Canton 3.7.3**), slot a-validator-1, OAuth2 via Keycloak.
SDKs: `@canton-network/wallet-sdk` 1.7.0 (JSON API) and `nodejumper-org/canton-rust-sdk@8d8a92b` (gRPC, and JSON in
the retry showcase). Repro: `README.md` (`pytest`). Showcases (sections at the end): scenarios S14 and S8.

| Scenario | TypeScript (DA) | Rust (Nodejumper) |
|---|---|---|
| S1 Connect: version + ledger end | PASS (no public `version()`) | PASS |
| S2 Allocate + list parties | PASS | PASS |
| S4 Create, read back from ACS | PASS | PASS |
| S5 Exercise non-consuming choice | PASS | PASS |
| S6 Value round-trip, 21 fields | PASS, byte-identical to reference | PASS, byte-identical to reference |
| S7 Error taxonomy | PASS | PASS |

**The headline is that both SDKs agree with the raw API on the basics.** That is a useful result in itself: 9 Standard rows
per SDK are now *Verified* on the coverage map. For Rust, these include 9 of its 50 self-claims, independently confirmed.

## Findings

1. **Nested `Optional`: the JSON Ledger API v2 differs from the Daml 2.x JSON spec, silently.** For
   `Optional (Optional a)`, the [Daml 2.x JSON API spec](https://github.com/digital-asset/docs.daml.com/blob/cc6128ace44610ed42876d063107bf9ffa4cc394/docs/2.10.6/docs/json-api/lf-value-specification.rst?plain=1#L345-L373) writes `None` as `null` and `Some None` as `[]`. The JSON
   Ledger API v2 uses one array level per Optional: `Some None` is `[[]]` and `[]` is `None`. The
   [current Canton docs](https://github.com/digital-asset/canton/blob/3694e3fb1cbb003008975ad4084381003e6e1af9/docs-open/src/sphinx/sdk/reference/json-api/daml-lf-json-encoding.csv?plain=1#L53) describe only a single-level `Optional`. A client that follows the 2.x rule therefore
   stores `None` when it means `Some None`, with no error, and gets an internal error when it sends `None`.

   | JSON sent for `Optional (Optional Text)` | Daml 2.x spec | JSON Ledger API v2 (Canton 3.7.3) |
   |---|---|---|
   | `null` | `None` | rejected, `LEDGER_API_INTERNAL_ERROR` |
   | `[]` | `Some None` | `None` |
   | `[[]]` | invalid for this type | `Some None` |

   Repro: `tools/repro/nested-optional.sh` (LocalNet slot a-validator-1, `curl` and `jq`). Peaceful Studio's C# SDK
   notes describe the same v2 rule.

2. **TS SDK: create does not return the contract id.** `sdk.ledger.internal.submit` uses submit-and-wait, which returns only
   `updateId`. Callers must find the contract through the ACS or a second call. Rust returns it.
3. **TS SDK: errors are thrown as plain objects, not `Error`s.** The Canton error body is all there (`code`, `grpcCodeValue`,
   `errorCategory`, `correlationId`), but `String(e)` and `e.message` give `[object Object]` and there is no stack.
4. **TS SDK: `client_credentials` always sends `scope=`, even when empty.** Keycloak rejects an empty scope (`invalid_scope`),
   so an SDK user must know to set a scope. Rust and the reference omit the empty parameter.
5. **TS SDK: no public `version()`.** The SDK calls `/v2/version` internally at init but does not expose it.
   Rust has `version()` and `health_check()`.
6. **TS SDK: a malformed static token fails locally** ("came back expired") before reaching the ledger. That is reasonable,
   but it is a different failure path from a server-side Unauthenticated. With a well-formed forged JWT, both SDKs surface
   Unauthenticated correctly.

None of these is severe. 2–5 are ergonomics, worth friendly issues to DA. 1 is the one to raise with the Standard's owners.

## Showcase: incomplete ACS reads
Repro: scenario S14, `LOCALNET_DIR=<checkout> .venv/bin/pytest -k S14` (about 25 s). A fresh party gets `Asset` contracts (party field `issuer`,
implements `Holding`) and `Marker` contracts (party field `owner`). Oracle: the reference reading every page of
`/v2/state/active-contracts-page`. Each TS read uses a fresh SDK instance.

| Read | 5 of each | 250 of each |
|---|---|---|
| Rust `active_contracts_with` (gRPC stream) | 5/5 | 250/250 |
| Rust `active_contracts_page_with`, every page | 5/5 | 250/250 |
| Rust stream, `Holding` interface | 5/5 | 250/250 |
| TS `acsReader.read` (default) | 5/5 | error `JSON_API_MAXIMUM_LIST_ELEMENTS_NUMBER_REACHED` |
| TS `acsReader.read({continueUntilCompletion: true})`, Asset | **0/5** | **0/250** |
| same, Marker (control) | 5/5 | 250/250 |
| TS `acsReader.paginated.raw.read` | 5/5 | 250/250 |
| TS `acsReader.raw.read`, `Holding` interface, `continueUntilCompletion` | **0/5** | **0/250** |
| TS default read on an instance that already read at 5 | 5/5 | 250/250 (a fresh instance errors) |

7. **TS: "read until complete" silently drops contracts.** With `continueUntilCompletion: true`,
   `AcsService.fetchActiveContractsUntilComplete` (`core-acs-reader`) rebuilds the ACS from `/v2/updates`. It keeps a
   created event only if `createArgument.owner` is one of the requested parties. A template whose party field has
   another name (`issuer`, `holder`, `registrar`) returns nothing, and no error is raised. The option exists to get
   *all* contracts, and the docstring promises that. `sdk.token.utxos.list({continueUntilCompletion: true})` uses the
   same path (`listContractsByInterface`), so token holdings of a template without an `owner` field disappear.
   Amulet has `owner` and is not affected. Rust and the paginated TS reader return everything.
8. **TS: without the option the default read stops working above the node's list limit (200).** The API answers 413
   as the OpenAPI spec says. That failure is loud, not silent, but it is a cliff that Rust's stream does not have.
9. **TS: `acsReader.read` caches by party and template and ignores the options.** On one SDK instance, the first
   call's options decide what later calls return. In our first run a default read at 5 contracts filled the cache,
   and the later `continueUntilCompletion` read then returned all 250 from it. Two apps with the same code can see
   different results depending on call order. S14's warm-cache case pins this down: at 250 contracts the same
   default read returns all of them on a warm instance (the cache only re-reads when it is empty, then follows
   `/v2/updates`) and fails with 413 on a fresh one.

## Showcase: retries and de-duplication
Repro: scenario S8, `LOCALNET_DIR=<checkout> .venv/bin/pytest -k S8` (about 35 s, stable over three runs). Fault proxy: `runner/faults.py`.
Every case runs on a fresh party. Oracle: the contract count read by the reference.

| Case | TS (JSON) | Rust, default | Rust with `RetryConfig::default()` |
|---|---|---|---|
| same command id twice, in turn | DUPLICATE_COMMAND, 1 contract | same | same |
| same id twice, concurrently (real participant) | **SUBMISSION_ALREADY_IN_FLIGHT, no retry** | SUBMISSION_ALREADY_IN_FLIGHT | retries, then DUPLICATE_COMMAND |
| `SEQUENCER_BACKPRESSURE` before execution | **fails after 1 attempt** | fails after 1 attempt | 2 attempts, committed |
| response lost after commit | REQUEST_TIMEOUT, 1 contract | REQUEST_TIMEOUT / Unknown, 1 contract | DUPLICATE_COMMAND, 1 contract |
| resubmit with the same id after the loss | DUPLICATE_COMMAND, still 1 | same | same |

10. **TS: the built-in retry never fires on the JSON API.** `core-ledger-client` retries POSTs 5 times, 3 s apart,
    when the error message contains `SEQUENCER_BACKPRESSURE`, `SUBMISSION_ALREADY_IN_FLIGHT` and four other codes.
    But `retryable()` passes every error through `asGrpcError()`, which re-throws anything without `code`,
    `message` and a `details` array. A JSON API error (`JsCantonError`: `code`, `cause`, `errorCategory`,
    `grpcCodeValue`, ...) has neither `message` nor `details`, so the error goes straight to the caller. This shows
    with a real participant, no fabricated error: two concurrent submits with one command id get
    `SUBMISSION_ALREADY_IN_FLIGHT` back in 0.4 s, after one attempt each. An app that relies on the SDK's retry
    under sequencer load gets failures instead.
11. **Agreement where it matters most.** No lane ever committed a command twice by itself. Resubmitting with the
    same command id is safe in both SDKs. In every lane, a lost response makes the app see a failure for a command
    that committed. Rust with retry reports it as `DUPLICATE_COMMAND`, as its docs say, and points to
    `Submission::recover`. In TS the app has to match the command id on the `sdk.events.completions()` stream
    itself (not exercised here). The control is that two submits without a
    command id commit twice in every lane, which is why an app must choose the command id itself.
