//! Generates the fixtures of scenario S11c (`scenarios/fixtures/max-record-time/`):
//! one prepared transaction with three deadlines, under V2 and V3.
//!
//! Usage: poc-max-record-time vectors/reject/reject-v2-max-record-time.json scenarios/fixtures/max-record-time
//! Writes each variant as a vector file, so every SDK adapter hashes the same bytes,
//! and prints one JSON line per variant with the Rust SDK's hash for reference.
use base64::Engine as _;
use canton_proto::com::daml::ledger::api::v2::interactive as ipb;
use prost::Message as _;
use serde_json::json;

const YEAR_MICROS: u64 = 365 * 24 * 3600 * 1_000_000;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let [_, vector, out_dir] = args.as_slice() else {
        eprintln!("usage: poc-max-record-time <vector.json> <out-dir>");
        std::process::exit(2);
    };
    let v: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(vector).expect("read vector")).expect("json");
    let b64 = base64::engine::general_purpose::STANDARD;
    let bytes = b64.decode(v["preparedTransaction"].as_str().expect("tx")).expect("base64");
    let shown = ipb::PreparedTransaction::decode(bytes.as_slice()).expect("decode");
    let deadline = shown
        .metadata
        .as_ref()
        .and_then(|m| m.max_record_time)
        .expect("the vector sets max_record_time");

    let variants = [
        ("shown-to-signer", Some(deadline)),
        ("deadline-removed", None),
        ("deadline-plus-1y", Some(deadline + YEAR_MICROS)),
    ];
    std::fs::create_dir_all(out_dir).expect("out dir");
    for (scheme_name, scheme) in [
        ("HASHING_SCHEME_VERSION_V2", ipb::HashingSchemeVersion::V2),
        ("HASHING_SCHEME_VERSION_V3", ipb::HashingSchemeVersion::V3),
    ] {
        for (name, max_record_time) in variants {
            let mut tx = shown.clone();
            tx.metadata.as_mut().expect("metadata").max_record_time = max_record_time;
            let id = format!("{name}-{}", &scheme_name[scheme_name.len() - 2..]).to_lowercase();
            let encoded = b64.encode(tx.encode_to_vec());
            let file = json!({"id": id, "variant": name, "scheme": scheme_name, "maxRecordTime": max_record_time,
                              "preparedTransaction": encoded});
            std::fs::write(format!("{out_dir}/{id}.json"), file.to_string()).expect("write");
            let result = canton_ledger::hashing::hash_prepared_transaction(&tx, scheme);
            let mut line = json!({"id": id, "variant": name, "scheme": scheme_name,
                                  "max_record_time": max_record_time});
            match result {
                Ok(h) => line["hash"] = h.iter().map(|b| format!("{b:02x}")).collect::<String>().into(),
                Err(e) => line["error"] = e.to_string().into(),
            }
            println!("{line}");
        }
    }
}
