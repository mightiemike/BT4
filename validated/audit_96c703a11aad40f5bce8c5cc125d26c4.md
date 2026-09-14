### Title
Uncontrolled Recursion in JSON-RPC Request Parsing Causes Unauthenticated Node Crash - ([File: chain/jsonrpc/src/lib.rs])

### Summary
The nearcore JSON-RPC server deserializes every incoming HTTP POST body directly into `Message` via axum's `Json<Message>` extractor, which uses `serde_json`'s recursive-descent parser under the hood. `serde_json::Value` (used for the untyped `params`/`result` fields) and the `Message`/`WireMessage` `#[serde(untagged)]` enums have no bound on nesting depth, so a small, deeply-nested JSON payload can drive the parser into unbounded recursion and overflow the call stack. In Rust, a stack overflow is an unrecoverable process abort, not a catchable panic — matching the exact bug class of CVE-2019-1010182 (`YamlLoader::load_from_str` uncontrolled recursion → impossible-to-catch abort), just with `serde_json` instead of `yaml-rust`.

### Finding Description
The RPC route registration binds the main endpoint to `rpc_handler`, which takes the request body as `Json<Message>`: [1](#0-0) 

`Message` and its component types embed `serde_json::Value` for arbitrary-shaped fields (`params`, `result`, `id`), and the top-level wire decoding wraps `Message` in an `#[serde(untagged)] enum WireMessage { Message(Message), Broken(Broken) }`, deserialized via `serde_json::de::from_slice`: [2](#0-1) 

`Request`/`Response` hold raw `Value` payloads: [3](#0-2) [4](#0-3) 

`serde_json`'s deserializer for `Value` recurses once per level of array/object nesting with no depth limit; there is no recursion-depth guard anywhere in this parsing path. The only mitigation present at this layer is a byte-size cap on the request body (`RequestBodyLimitLayer::new(limits_config.json_payload_max_size)`): [5](#0-4) 

A byte-size limit does not bound nesting depth — a payload such as `{"jsonrpc":"2.0","id":1,"method":"x","params":` + `"["*N` + `"]"*N` + `}` can encode tens of thousands of nesting levels in only a few dozen KB, well under any reasonable `json_payload_max_size`. Because `WireMessage` is `#[serde(untagged)]`, `serde_json` will additionally attempt to re-parse/re-validate the same nested value against multiple variant shapes (`Message` vs `Broken`), which increases stack consumption per nesting level compared to a single straightforward recursive parse, making the overflow reachable at shallower nesting depth than a naive `serde_json::Value` parse alone.

This is architecturally the same root cause as CVE-2019-1010182: an untrusted, attacker-controlled document is parsed via unbounded structural recursion, and the parser has no depth cap, so a maliciously crafted (but otherwise syntactically valid) document forces a stack overflow — which aborts the process rather than raising a catchable error.

### Impact Explanation
A stack overflow in a native Rust binary triggers a hard process abort (SIGABRT/segfault), not a recoverable `Result`/`panic!` that existing `panic = "abort"`/`catch_unwind` boundaries can intercept. Any node exposing the JSON-RPC HTTP endpoint (validators, RPC nodes, indexers) can be crashed by any unauthenticated network peer capable of sending an HTTP POST to `/`. This is a transaction/RPC-triggered halt of the node process — for a validator this directly disrupts block production and consensus participation, and for RPC infrastructure it is a straightforward denial-of-service that requires no signed transaction, no gas payment, and no privileged access.

### Likelihood Explanation
High. The JSON-RPC `/` endpoint is intentionally public and unauthenticated (used by wallets, exchanges, indexers, dApps). Constructing a deeply nested JSON payload is trivial (a few lines of script), and the byte-size body limit does not meaningfully restrict nesting depth, since one nesting level costs only 1–2 bytes (`[`/`]`). No account, stake, or gas is required — a single anonymous HTTP request suffices.

### Recommendation
- Enforce an explicit maximum JSON nesting depth before/while parsing any untrusted RPC body, independent of the byte-size limit (e.g., wrap `serde_json::Deserializer` with a depth-tracking `Deserializer::from_slice` configuration, or pre-scan for bracket/brace depth and reject if it exceeds a safe bound, e.g. 64–128 levels, before calling into `serde_json`).
- Apply the same depth cap uniformly to all paths that deserialize attacker-supplied JSON into `serde_json::Value`, including `params`, `result`, and any nested `Value` fields reachable from RPC/query/view-state responses.
- Consider running the JSON deserialization for the public RPC endpoint on a dedicated thread with a bounded, guarded stack (or with `stacker`-style guard pages) so that a stack overflow, if it still occurs, can be converted into a recoverable error rather than aborting the whole process.
- Add a regression test that posts a payload with thousands of nested arrays/objects to `/` and asserts the server returns a JSON-RPC parse error instead of crashing.

### Proof of Concept
```python
import requests

depth = 200_000
payload = (
    b'{"jsonrpc":"2.0","id":1,"method":"query","params":'
    + b'[' * depth
    + b']' * depth
    + b'}'
)

# Node process aborts (stack overflow) while axum's Json<Message> extractor
# invokes serde_json to deserialize the untagged WireMessage/Message/Value graph.
requests.post("http://TARGET_NODE:3030/", data=payload,
               headers={"Content-Type": "application/json"})
```
The payload is well under typical `json_payload_max_size` limits (a few hundred KB at most) but contains enough nested array levels to exhaust the default OS thread stack during recursive descent parsing in `serde_json`, crashing the node process handling the request.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L2928-2938)
```rust
async fn rpc_handler(
    State(handler): State<Arc<JsonRpcHandler>>,
    headers: axum::http::HeaderMap,
    Json(request): Json<Message>,
) -> Response {
    let source = if headers.contains_key(SHARDED_RPC_COORDINATOR_HEADER) {
        RequestSource::Coordinator
    } else {
        RequestSource::User
    };
    let message = handler.process(request.clone(), source).await;
```

**File:** chain/jsonrpc/src/lib.rs (L3258-3260)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** chain/jsonrpc-primitives/src/message.rs (L49-57)
```rust
#[derive(Debug, serde::Serialize, serde::Deserialize, Clone, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Request {
    jsonrpc: Version,
    pub method: String,
    #[serde(default, skip_serializing_if = "Value::is_null")]
    pub params: Value,
    pub id: Value,
}
```

**File:** chain/jsonrpc-primitives/src/message.rs (L95-115)
```rust
/// Deserializer for `Option<Value>` that produces `Some(Value::Null)`.
///
/// The usual one produces None in that case. But we need to know the difference between
/// `{x: null}` and `{}`.
fn some_value<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Option<Value>, D::Error> {
    serde::Deserialize::deserialize(deserializer).map(Some)
}

/// A helper trick for deserialization.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct WireResponse {
    // It is actually used to eat and sanity check the deserialized text
    #[allow(dead_code)]
    jsonrpc: Version,
    // Make sure we accept null as Some(Value::Null), instead of going to None
    #[serde(default, deserialize_with = "some_value")]
    result: Option<Value>,
    error: Option<RpcError>,
    id: Value,
}
```

**File:** chain/jsonrpc-primitives/src/message.rs (L240-264)
```rust
/// A trick to easily deserialize and detect valid JSON, but invalid Message.
#[derive(serde::Deserialize)]
#[serde(untagged)]
pub enum WireMessage {
    Message(Message),
    Broken(Broken),
}

pub fn decoded_to_parsed(res: JsonResult<WireMessage>) -> Parsed {
    match res {
        Ok(WireMessage::Message(Message::UnmatchedSub(value))) => Err(Broken::Unmatched(value)),
        Ok(WireMessage::Message(m)) => Ok(m),
        Ok(WireMessage::Broken(b)) => Err(b),
        Err(e) => Err(Broken::SyntaxError(e.to_string())),
    }
}

pub type Parsed = Result<Message, Broken>;

/// Read a [Message](enum.Message.html) from a slice.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}
```
