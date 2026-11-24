// Conformance adapter for @canton-network/wallet-sdk (JSON Ledger API), pinned in package.json.
// Driver protocol: one JSON request per stdin line, one JSON response per stdout line.
// Uses only the SDK's public surface: SDK.create, sdk.ledger.*, sdk.party.*, and
// computePreparedTransactionHash from @canton-network/core-tx-hashing (what sdk.utils.hash uses).
import { createInterface } from 'node:readline'
import { SDK } from '@canton-network/wallet-sdk'
import { computePreparedTransactionHash } from '@canton-network/core-tx-hashing'

// Silence SDK logging on stdout (the protocol channel).
const origLog = console.log
console.log = (...a) => process.stderr.write(a.join(' ') + '\n')
const send = (o) => process.stdout.write(JSON.stringify(o) + '\n')

// tagged neutral value -> Daml-LF JSON encoding
function lf(v) {
    const [[tag, x]] = Object.entries(v)
    switch (tag) {
        case 'party': case 'text': case 'int64': case 'numeric': case 'date':
        case 'timestamp': case 'contractId': case 'bool': case 'enum':
            return x
        case 'unit': return {}
        case 'optional': // JSON Ledger API v2: a nested Optional chain is one array level per Optional
            if (x === null) return null
            return 'optional' in x ? arr(v) : lf(x)
        case 'list': return x.map(lf)
        case 'textMap': return Object.fromEntries(Object.entries(x).map(([k, w]) => [k, lf(w)]))
        case 'genMap': return x.map(([k, w]) => [lf(k), lf(w)])
        case 'record': return Object.fromEntries(x.map(([k, w]) => [k, lf(w)]))
        case 'variant': return { tag: x.constructor, value: lf(x.value) }
        default: throw new Error(`unknown tag ${tag}`)
    }
}
function arr(v) {
    const x = v.optional
    if (x === null) return []
    return 'optional' in x ? [arr(x)] : [lf(x)]
}
const tid = (t) => `${t.package}:${t.module}:${t.entity}`

// Map whatever the SDK throws to the protocol's error shape, looking only at what the SDK exposes.
function dump(x, depth = 0) {
    if (x === null || typeof x !== 'object' || depth > 4) return x
    const o = {}
    for (const k of Object.getOwnPropertyNames(x)) if (k !== 'stack') o[k] = dump(x[k], depth + 1)
    return o
}
function findKey(x, keys, depth = 0) {
    if (x === null || typeof x !== 'object' || depth > 5) return undefined
    for (const k of keys) if (typeof x[k] === 'string' || typeof x[k] === 'number') return x[k]
    for (const v of Object.values(x)) {
        const r = findKey(v, keys, depth + 1)
        if (r !== undefined) return r
    }
    return undefined
}
const GRPC = ['Ok','Cancelled','Unknown','InvalidArgument','DeadlineExceeded','NotFound','AlreadyExists','PermissionDenied',
    'ResourceExhausted','FailedPrecondition','Aborted','OutOfRange','Unimplemented','Internal','Unavailable','DataLoss','Unauthenticated']
function errJson(e) {
    const d = dump(e)
    const grpc = findKey(d, ['grpcCodeValue'])
    const code = findKey(d, ['code'])
    return {
        status: typeof grpc === 'number' ? GRPC[grpc] : (findKey(d, ['status']) ?? null),
        errorId: typeof code === 'string' && /^[A-Z_]+$/.test(code) ? code : null,
        message: String(e?.message ?? e).slice(0, 600),
        raw: JSON.stringify(d).slice(0, 3000),
    }
}


let sdk
async function handle(req) {
    switch (req.op) {
        case 'hash': {
            const opts = req.limits ? { limits: req.limits } : undefined
            try {
                const h = await computePreparedTransactionHash(req.preparedTransaction, req.scheme, opts)
                return { hash: Buffer.from(h).toString('hex') }
            } catch (e) {
                throw Object.assign(new Error(`${e?.code ?? 'ERROR'}: ${e?.message ?? e}`), { code: e?.code })
            }
        }
        case 'init': {
            const c = req.config
            const auth = req.token
                ? { method: 'static', token: req.token }
                : { method: 'client_credentials', configUrl: c.oidcConfigUrl,
                    credentials: { clientId: c.clientId, clientSecret: c.clientSecret, audience: c.audience, scope: c.scope ?? 'openid' } }
            sdk = await SDK.create({ auth, ledgerClientUrl: new URL(c.jsonUrl), synchronizerId: c.synchronizerId })
            return { sdk: '@canton-network/wallet-sdk@1.7.0', transport: 'json' }
        }
        case 'version':
            return { unsupported: true, reason: 'no public version() on the SDK' }
        case 'ledger_end':
            return { offset: Number(await sdk.ledger.ledgerEnd()) }
        case 'allocate_party':
            return { party: await sdk.party.internal.allocate({ partyHint: req.hint }) }
        case 'list_parties':
            return { parties: await sdk.party.list() }
        case 'create': {
            const r = await sdk.ledger.internal.submit({
                commands: [{ CreateCommand: { templateId: tid(req.template), createArguments: Object.fromEntries(req.args.map(([k, w]) => [k, lf(w)])) } }],
                actAs: [req.actAs],
            })
            return { contractId: null, updateId: r.updateId }
        }
        case 'submit': {
            // create with a caller-chosen command id (submit-and-wait); the SDK's own retry applies
            const r = await sdk.ledger.internal.submit({
                commands: [{ CreateCommand: { templateId: tid(req.template), createArguments: Object.fromEntries(req.args.map(([k, w]) => [k, lf(w)])) } }],
                actAs: [req.actAs],
                ...(req.commandId ? { commandId: req.commandId } : {}),
            })
            return { updateId: r.updateId }
        }
        case 'exercise': {
            const r = await sdk.ledger.internal.submit({
                commands: [{ ExerciseCommand: { templateId: tid(req.template), contractId: req.contractId, choice: req.choice, choiceArgument: lf(req.arg) } }],
                actAs: [req.actAs],
            })
            return { updateId: r.updateId }
        }
        case 'acs': {
            // mode "default": acsReader.read as documented; "complete": with continueUntilCompletion: true;
            // "paged": acsReader.paginated.raw.read with continueUntilCompletion: true (every page).
            // With req.interface, filters by interface through acsReader.raw.read, as the token-standard holdings listing does.
            const filter = req.interface ? { interfaceIds: [tid(req.interface)] } : { templateIds: [tid(req.template)] }
            const opts = { parties: [req.party], filterByParty: true, ...filter }
            const complete = { ...opts, continueUntilCompletion: true }
            const rows = req.interface
                ? await sdk.ledger.acsReader.raw.read(req.mode === 'complete' ? complete : opts)
                : req.mode === 'complete'
                  ? await sdk.ledger.acsReader.read(complete)
                  : req.mode === 'paged'
                    ? await sdk.ledger.acsReader.paginated.raw.read(complete)
                    : await sdk.ledger.acsReader.read(opts)
            return {
                contracts: rows
                    .map((r) => r.contractEntry?.JsActiveContract?.createdEvent)
                    .filter(Boolean)
                    .map((ev) => ({ contractId: ev.contractId, args: ev.createArgument })),
            }
        }
        default:
            throw Object.assign(new Error(`unsupported op ${req.op}`), { unsupported: true })
    }
}

const rl = createInterface({ input: process.stdin })
for await (const line of rl) {
    const req = JSON.parse(line)
    try {
        const result = await handle(req)
        send({ id: req.id, ok: true, result })
    } catch (e) {
        send({ id: req.id, ok: false, error: errJson(e) })
    }
}
