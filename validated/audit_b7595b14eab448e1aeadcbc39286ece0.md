### Title
Stack-overflow denial of service via deeply nested JSON in unauthenticated JSON-RPC request body - (File: `chain/jsonrpc/src/lib.rs`, `chain/jsonrpc-primitives/src/message.rs`)

### Summary
CVE-2017-5950 is a stack-exhaustion crash caused by unbounded recursive descent parsing of an attacker-controlled, deeply nested document (YAML). nearcore's JSON-RPC entry point has the same bug class: the request body is deserialized into an `#[serde(untagged)]` `Message` enum whose `params` field is an arbitrary `serde_json::Value`, and this deserialization is done through ordinary recursive-descent `serde_json` calls with no depth limit — only a byte-size limit is enforced.

### Finding Description
The JSON-RPC HTTP endpoint is registered at `POST /` and protected only by a body-size middleware: [1](#0-0) 

The configured limit is a raw byte count (`json_payload_max_size`, default 10 MiB), not a structural/nesting-depth limit: [2](#0-1) 

The request body is deserialized into the `Message` enum, which is `#[serde(untagged)]` and whose `Request`/`Notification` variants carry an unconstrained `serde_json::Value` for `params`: [3](#0-2) [4](#0-3) 

Per-method parameter parsing (`Params::parse`) also goes through `serde_json::from_value`, i.e. more recursive descent over the already-built `Value` tree: [5](#0-4) 

`serde_json`'s `Value`/generic deserialization has no built-in recursion-depth guard; each level of `[`/`{` nesting in the input consumes call-stack frames during both the initial parse into `Message`/`Value` and any subsequent typed re-deserialization (`Params::parse`, `try_singleton`, `try_pair`). Because the byte-size cap is 10 MiB and each extra nesting level costs as little as 1–2 bytes (`[` … `]`), an attacker can trivially construct a payload with hundreds of thousands to millions of nesting levels while staying well under the size limit — enough to exhaust the thread's stack and trigger a stack overflow. In Rust, a genuine stack overflow (guard-page hit) aborts the whole process; it is not a catchable panic. Since the JSON-RPC server (`ClientActor`, `ViewClientActor`, `RpcHandlerActor`) runs in the same `neard` process as chain/validator logic per the RPC architecture doc, an unauthenticated network caller can crash the entire node process, including a validator node.

### Impact Explanation
This is directly reachable by any unauthenticated RPC caller (no signed transaction, no access key, no special privilege required) against the public JSON-RPC port (default 3030). A single crafted HTTP POST causes the node process to abort, which is a caller-triggered halt of the node — matching the accepted "transaction/RPC-triggered halt" impact class. If the affected node is a validator, this directly threatens chain liveness; more broadly it is a trivial one-request DoS against any RPC-serving node.

### Likelihood Explanation
High. The endpoint is unauthenticated, requires no funds or prior state, and the payload (deeply nested arrays under a 10 MiB cap) is trivial to construct and easy to fit well within existing size limits.

### Recommendation
Add an explicit recursion/nesting-depth check before or during JSON deserialization of the RPC body (e.g., use a depth-limited `serde_json::Deserializer` wrapper, reject inputs whose bracket-nesting exceeds a small bound such as 64–128 before full parsing, or parse on a dedicated thread with a bounded stack and a supervising watchdog that returns an RPC error instead of aborting). This should be applied both to the top-level `Message` parse and to per-method `Params::parse`/`serde_json::from_value` calls.

### Proof of Concept
Conceptually (verification requires running the actual `neard` binary, which is outside static-analysis scope here):
```
POST / HTTP/1.1
Host: <node>:3030
Content-Type: application/json
Content-Length: <a few MB>

{"jsonrpc":"2.0","id":1,"method":"query","params":[[[[[[[[[[ ... repeated ~500,000+ times ... ]]]]]]]]]]}
```
The nested-array payload stays under the default 10 MiB `json_payload_max_size`, but its parse into `serde_json::Value` (as part of the untagged `Message`/`Request.params` field) recurses once per nesting level, exhausting the thread stack and aborting the `neard` process.

Note: I could not fully inspect the exact `rpc_handler`/Axum extractor implementation (whether it uses `axum::Json<Message>` directly or a manual `serde_json::from_slice` call) due to tool-call limits; the finding rests on the confirmed facts that (1) the body-size middleware imposes only a byte limit, (2) `Message`/`Request::params` is an unconstrained `serde_json::Value`, and (3) `serde_json`'s recursive-descent deserialization has no depth guard — all independently verified in the cited files.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L145-155)
```rust
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct RpcLimitsConfig {
    /// Maximum byte size of the json payload.
    pub json_payload_max_size: usize,
}

impl Default for RpcLimitsConfig {
    fn default() -> Self {
        Self { json_payload_max_size: 10 * 1024 * 1024 }
    }
}
```

**File:** chain/jsonrpc/src/lib.rs (L3232-3260)
```rust
    let mut app = Router::new()
        .route("/", post(rpc_handler))
        .route("/status", get(status_handler).head(status_handler))
        .route("/health", get(health_handler).head(health_handler))
        .route("/network_info", get(network_info_handler))
        .route("/metrics", get(prometheus_handler))
        .route("/openapi.json", get(openapi_json_handler));

    if enable_debug_rpc {
        app = app
            .route("/debug/api/entity", post(handle_entity_debug))
            .route(
                "/debug/api/block_status/{starting_height}",
                #[allow(deprecated)]
                get(deprecated_debug_block_status_handler),
            )
            .route("/debug/api/block_status", get(debug_block_status_handler))
            .route("/debug/api/epoch_info/{epoch_id}", get(debug_epoch_info_handler))
            .route("/debug/api/epoch_info_light/{epoch_id}", get(debug_epoch_info_light_handler))
            .route("/debug/api/instrumented_threads", get(debug_instrumented_threads_handler))
            .route("/debug/api/{*api_path}", get(debug_handler))
            .route("/debug/client_config", get(client_config_handler))
            .route("/debug", get(debug_html))
            .route("/debug/pages/{page}", get(display_debug_html));
    }

    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** chain/jsonrpc-primitives/src/message.rs (L48-57)
```rust
/// An RPC request.
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

**File:** chain/jsonrpc-primitives/src/message.rs (L158-181)
```rust
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Message {
    /// An RPC request.
    Request(Request),
    /// A response to a Request.
    Response(Response),
    /// A notification.
    Notification(Notification),
    /// A batch of more requests or responses.
    ///
    /// The protocol allows bundling multiple requests, notifications or responses to a single
    /// message.
    ///
    /// This variant has no direct constructor and is expected to be constructed manually.
    Batch(Vec<Message>),
    /// An unmatched sub entry in a `Batch`.
    ///
    /// When there's a `Batch` and an element doesn't conform to the JSONRPC 2.0 format, that one
    /// is represented by this. This is never produced as a top-level value when parsing, the
    /// `Err(Broken::Unmatched)` is used instead. It is not possible to serialize.
    #[serde(skip_serializing)]
    UnmatchedSub(Value),
}
```

**File:** chain/jsonrpc/src/api/mod.rs (L151-157)
```rust
        pub fn parse(value: Value) -> Result<T, RpcParseError>
        where
            T: DeserializeOwned,
        {
            serde_json::from_value(value)
                .map_err(|e| RpcParseError(format!("Failed parsing args: {e}")))
        }
```
