//! Conformance adapter for nodejumper-org/canton-rust-sdk (gRPC, and JSON where the runner asks for it).
//! Driver protocol: one JSON request per stdin line, one JSON response per stdout line.
//! Values arrive in the runner's tagged neutral form and are returned in Daml-LF JSON encoding.
use base64::Engine as _;
use canton_admin::AdminClient;
use canton_auth::{OidcConfig, TokenProvider};
use canton_ledger::proto as pb;
use canton_ledger::{
    ActiveContractsRequest, CantonClient, Config, JsonClient, JsonCommands, RetryConfig, Submit, create,
    exercise, identifier,
};
use canton_proto::com::daml::ledger::api::v2::interactive as ipb;
use futures::StreamExt as _;
use pb::value::Sum;
use prost::Message as _;
use serde_json::{Map, Value as J, json};
use tokio::io::{AsyncBufReadExt as _, BufReader};

#[derive(Default)]
struct State {
    ctx: Option<Ctx>,
}

struct Ctx {
    ledger: CantonClient,
    admin: AdminClient,
    /// Set when the runner asks for `"transport": "json"`; only `submit` uses it.
    json: Option<JsonClient>,
}

fn token_provider(c: &J) -> TokenProvider {
    let oidc = OidcConfig::new(
        c["tokenUrl"].as_str().unwrap(),
        c["clientId"].as_str().unwrap(),
        c["clientSecret"].as_str().unwrap(),
    )
    .with_audience(c["audience"].as_str().unwrap());
    TokenProvider::new(oidc)
}

/// `"retry": true` in the config turns on the SDK's retry with its defaults; otherwise none, the SDK default.
fn config(c: &J, token_override: Option<&str>) -> Config {
    let mut cfg = Config::new(c["grpcEndpoint"].as_str().unwrap());
    if c["retry"].as_bool() == Some(true) {
        cfg = cfg.with_retry(RetryConfig::default());
    }
    match token_override {
        Some(t) => cfg.with_token(t),
        None => cfg.with_oidc(token_provider(c)),
    }
}

fn json_client(c: &J) -> JsonClient {
    let mut client = JsonClient::new(c["jsonUrl"].as_str().unwrap()).with_oidc(token_provider(c));
    if c["retry"].as_bool() == Some(true) {
        client = client.with_retry(RetryConfig::default());
    }
    client
}

// ---- tagged neutral value -> proto ----
fn to_pb(v: &J) -> pb::Value {
    let (tag, x) = v.as_object().unwrap().iter().next().unwrap();
    let sum = match tag.as_str() {
        "party" => Sum::Party(x.as_str().unwrap().into()),
        "text" => Sum::Text(x.as_str().unwrap().into()),
        "int64" => Sum::Int64(x.as_str().unwrap().parse().unwrap()),
        "numeric" => Sum::Numeric(x.as_str().unwrap().into()),
        "bool" => Sum::Bool(x.as_bool().unwrap()),
        "date" => {
            let d = chrono::NaiveDate::parse_from_str(x.as_str().unwrap(), "%Y-%m-%d").unwrap();
            Sum::Date((d - chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap()).num_days() as i32)
        }
        "timestamp" => Sum::Timestamp(
            chrono::DateTime::parse_from_rfc3339(x.as_str().unwrap()).unwrap().timestamp_micros(),
        ),
        "unit" => Sum::Unit(()),
        "contractId" => Sum::ContractId(x.as_str().unwrap().into()),
        "optional" => Sum::Optional(Box::new(pb::Optional {
            value: if x.is_null() { None } else { Some(Box::new(to_pb(x))) },
        })),
        "list" => Sum::List(pb::List { elements: x.as_array().unwrap().iter().map(to_pb).collect() }),
        "textMap" => Sum::TextMap(pb::TextMap {
            entries: x.as_object().unwrap().iter()
                .map(|(k, v)| pb::text_map::Entry { key: k.clone(), value: Some(to_pb(v)) })
                .collect(),
        }),
        "genMap" => Sum::GenMap(pb::GenMap {
            entries: x.as_array().unwrap().iter()
                .map(|kv| pb::gen_map::Entry { key: Some(to_pb(&kv[0])), value: Some(to_pb(&kv[1])) })
                .collect(),
        }),
        "record" => Sum::Record(to_record(x)),
        "variant" => Sum::Variant(Box::new(pb::Variant {
            variant_id: None,
            constructor: x["constructor"].as_str().unwrap().into(),
            value: Some(Box::new(to_pb(&x["value"]))),
        })),
        "enum" => Sum::Enum(pb::Enum { enum_id: None, constructor: x.as_str().unwrap().into() }),
        other => panic!("unknown tag {other}"),
    };
    pb::Value { sum: Some(sum) }
}

fn to_record(fields: &J) -> pb::Record {
    pb::Record {
        record_id: None,
        fields: fields.as_array().unwrap().iter()
            .map(|f| pb::RecordField { label: f[0].as_str().unwrap().into(), value: Some(to_pb(&f[1])) })
            .collect(),
    }
}

// ---- proto -> Daml-LF JSON encoding ----
fn canon(v: &pb::Value) -> J {
    match v.sum.as_ref() {
        None => J::Null,
        Some(s) => match s {
            Sum::Party(x) | Sum::Text(x) | Sum::Numeric(x) | Sum::ContractId(x) => json!(x),
            Sum::Int64(i) => json!(i.to_string()),
            Sum::Bool(b) => json!(b),
            Sum::Unit(()) => json!({}),
            Sum::Date(d) => json!((chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap()
                + chrono::Duration::days(*d as i64)).format("%Y-%m-%d").to_string()),
            Sum::Timestamp(us) => json!(chrono::DateTime::from_timestamp_micros(*us).unwrap()
                .format("%Y-%m-%dT%H:%M:%S%.6fZ").to_string()),
            // JSON Ledger API v2: a nested Optional chain is one array level per Optional
            Sum::Optional(o) => match o.value.as_deref() {
                None => J::Null,
                Some(inner) => match inner.sum.as_ref() {
                    Some(Sum::Optional(_)) => arr(o),
                    _ => canon(inner),
                },
            },
            Sum::List(l) => J::Array(l.elements.iter().map(canon).collect()),
            Sum::TextMap(m) => J::Object(m.entries.iter()
                .map(|e| (e.key.clone(), e.value.as_ref().map(canon).unwrap_or(J::Null))).collect()),
            Sum::GenMap(m) => J::Array(m.entries.iter()
                .map(|e| json!([e.key.as_ref().map(canon), e.value.as_ref().map(canon)])).collect()),
            Sum::Record(r) => record_json(r),
            Sum::Variant(var) => json!({"tag": var.constructor, "value": var.value.as_deref().map(canon)}),
            Sum::Enum(e) => json!(e.constructor),
        },
    }
}

fn arr(o: &pb::Optional) -> J {
    match o.value.as_deref() {
        None => json!([]),
        Some(inner) => match inner.sum.as_ref() {
            Some(Sum::Optional(io)) => json!([arr(io)]),
            _ => json!([canon(inner)]),
        },
    }
}

fn record_json(r: &pb::Record) -> J {
    let mut m = Map::new();
    for (i, f) in r.fields.iter().enumerate() {
        let k = if f.label.is_empty() { format!("_{i}") } else { f.label.clone() };
        m.insert(k, f.value.as_ref().map(canon).unwrap_or(J::Null));
    }
    J::Object(m)
}

fn tid(t: &J) -> pb::Identifier {
    identifier(t["package"].as_str().unwrap(), t["module"].as_str().unwrap(), t["entity"].as_str().unwrap())
}

fn err_json(e: &canton_ledger::Error) -> J {
    let info = e.error_info();
    json!({
        "status": e.code().map(|c| format!("{c:?}")),
        "errorId": info.as_ref().map(|i| i.reason.clone()),
        "retriable": e.is_retriable(),
        "message": e.to_string(),
    })
}

fn scheme(name: &str) -> Result<ipb::HashingSchemeVersion, J> {
    ipb::HashingSchemeVersion::from_str_name(name).ok_or(json!({"message": format!("unsupported scheme {name}")}))
}

fn decode_tx(b64: &str) -> Result<ipb::PreparedTransaction, J> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| json!({"message": format!("base64: {e}")}))?;
    ipb::PreparedTransaction::decode(bytes.as_slice()).map_err(|e| json!({"message": format!("decode: {e}")}))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// `canton_ledger::hashing::hash_prepared_transaction` after `prost` decoding.
fn hash_op(req: &J) -> Result<J, J> {
    if !req["limits"].is_null() {
        return Ok(json!({"unsupported": true, "reason": "no configurable limits (fixed depth limit of 256)"}));
    }
    let scheme = scheme(req["scheme"].as_str().unwrap_or(""))?;
    let tx = decode_tx(req["preparedTransaction"].as_str().unwrap_or(""))?;
    match std::panic::catch_unwind(|| canton_ledger::hashing::hash_prepared_transaction(&tx, scheme)) {
        Ok(Ok(h)) => Ok(json!({"hash": hex(&h)})),
        Ok(Err(e)) => Err(json!({"message": e.to_string()})),
        Err(_) => Err(json!({"message": "PANIC"})),
    }
}

async fn handle(state: &mut State, req: &J) -> Result<J, J> {
    let op = req["op"].as_str().unwrap_or("");
    match op {
        "hash" => return hash_op(req),
        _ => {}
    }
    let ctx = &mut state.ctx;
    if op == "init" {
        let c = &req["config"];
        let token = req["token"].as_str();
        let ledger = CantonClient::connect_lazy(config(c, token)).map_err(|e| err_json(&e))?;
        let admin = AdminClient::connect_lazy(config(c, token)).map_err(|e| err_json(&e))?;
        let json = (c["transport"] == "json").then(|| json_client(c));
        let transport = if json.is_some() { "json" } else { "grpc" };
        *ctx = Some(Ctx { ledger, admin, json });
        return Ok(json!({"sdk": "canton-rust-sdk@8d8a92b", "transport": transport,
                         "retry": c["retry"].as_bool() == Some(true)}));
    }
    let c = ctx.as_ref().ok_or(json!({"message": "init first"}))?;
    let e = |e: canton_ledger::Error| err_json(&e);
    match op {
        "version" => Ok(json!({"version": c.ledger.version().await.map_err(e)?})),
        "ledger_end" => Ok(json!({"offset": c.ledger.ledger_end().await.map_err(e)?})),
        "allocate_party" => {
            let p = c.admin.allocate_party(req["hint"].as_str()).await.map_err(e)?;
            Ok(json!({"party": p.party}))
        }
        "list_parties" => {
            let ps = c.admin.list_known_parties().await.map_err(e)?;
            Ok(json!({"parties": ps.into_iter().map(|p| p.party).collect::<Vec<_>>()}))
        }
        "create" => {
            let cmd = create(tid(&req["template"]), to_record(&req["args"]));
            let tx = c.ledger
                .submit_and_wait_for_transaction(Submit::new(req["actAs"].as_str().unwrap()).add_command(cmd))
                .await.map_err(e)?;
            let cid = tx.events.iter().find_map(|ev| match ev.event.as_ref()? {
                pb::event::Event::Created(cr) => Some(cr.contract_id.clone()),
                _ => None,
            });
            Ok(json!({"contractId": cid, "updateId": tx.update_id}))
        }
        "submit" => {
            // create with a caller-chosen command id, through submit-and-wait-for-transaction on either lane
            let act_as = req["actAs"].as_str().unwrap();
            let command_id = req["commandId"].as_str();
            if let Some(json) = &c.json {
                let t = &req["template"];
                let template = format!("{}:{}:{}", t["package"].as_str().unwrap(), t["module"].as_str().unwrap(),
                                       t["entity"].as_str().unwrap());
                let mut cmds = JsonCommands::new(vec![act_as.into()])
                    .add_create(template, record_json(&to_record(&req["args"])));
                if let Some(id) = command_id {
                    cmds = cmds.with_command_id(id);
                }
                let r = json.submit_and_wait_for_transaction(&cmds).await.map_err(e)?;
                return Ok(json!({"updateId": r.transaction.update_id}));
            }
            let mut submit = Submit::new(act_as).add_command(create(tid(&req["template"]), to_record(&req["args"])));
            if let Some(id) = command_id {
                submit = submit.with_command_id(id);
            }
            let tx = c.ledger.submit_and_wait_for_transaction(submit).await.map_err(e)?;
            Ok(json!({"updateId": tx.update_id}))
        }
        "exercise" => {
            let cmd = exercise(tid(&req["template"]), req["contractId"].as_str().unwrap(),
                               req["choice"].as_str().unwrap(), to_pb(&req["arg"]));
            let r = c.ledger
                .submit_and_wait(Submit::new(req["actAs"].as_str().unwrap()).add_command(cmd))
                .await.map_err(e)?;
            Ok(json!({"updateId": r.update_id}))
        }
        "acs" => {
            // mode "default" and "complete": the gRPC stream, which is the SDK's one way to read everything;
            // "paged": every page of active_contracts_page_with. With req.interface, filters by interface.
            let at = c.ledger.ledger_end().await.map_err(e)?;
            let name = |t: &J| format!("{}:{}:{}", t["package"].as_str().unwrap(), t["module"].as_str().unwrap(),
                                       t["entity"].as_str().unwrap());
            let rq = ActiveContractsRequest::new(vec![req["party"].as_str().unwrap().into()], at);
            let rq = if req["interface"].is_object() {
                rq.for_interfaces([name(&req["interface"])]).map_err(e)?
            } else {
                rq.for_templates([name(&req["template"])]).map_err(e)?
            };
            let row = |ce: pb::CreatedEvent| json!({"contractId": ce.contract_id,
                                                    "args": ce.create_arguments.as_ref().map(record_json)});
            let mut out = vec![];
            if req["mode"] == "paged" {
                let mut token = None;
                loop {
                    let (page, next) = c.ledger.active_contracts_page_with(&rq, 100, token).await.map_err(e)?;
                    out.extend(page.into_iter().filter_map(|ac| ac.created_event).map(row));
                    match next {
                        Some(t) => token = Some(t),
                        None => break,
                    }
                }
            } else {
                let mut s = Box::pin(c.ledger.active_contracts_with(rq).await.map_err(e)?);
                while let Some(item) = s.next().await {
                    if let Some(ce) = item.map_err(e)?.created_event {
                        out.push(row(ce));
                    }
                }
            }
            Ok(json!({"contracts": out}))
        }
        other => Err(json!({"message": format!("unsupported op {other}"), "unsupported": true})),
    }
}

#[tokio::main]
async fn main() {
    let mut state = State::default();
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let req: J = serde_json::from_str(&line).unwrap();
        let resp = match handle(&mut state, &req).await {
            Ok(r) => json!({"id": req["id"], "ok": true, "result": r}),
            Err(e) => json!({"id": req["id"], "ok": false, "error": e}),
        };
        println!("{resp}");
    }
}
