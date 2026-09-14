### Title
Unbounded JSON parsing depth in the JSON-RPC endpoint enables stack-overflow / node crash DoS - ([File: chain/jsonrpc/src/lib.rs])

### Summary
The NEAR JSON-RPC HTTP server deserializes every incoming request body directly into a `Message` value using Axum's `Json<Message>` extractor (backed by `serde_json`), before any authentication, method routing, or semantic validation takes place. `serde_json`'s deserializer is a recursive-descent parser with no built-in nesting-depth limit, and `Message`/`Request`/`Response` embed a raw `serde_json::Value` for `params`/`result`/`id` [1](#0-0) . `Value`'s `Deserialize` implementation recurses once per nesting level of arrays/objects, so a deeply nested JSON payload (e.g. millions of `[` characters) drives the parser into unbounded recursion.

### Finding Description
This is the same bug class as CVE-2025-65519 (unvalidated JSON nesting depth causing CPU/stack exhaustion), mapped onto nearcore's JSON-RPC ingestion path:

- `rpc_handler()` extracts the body as `Json<Message>` and immediately calls `handler.process(request.clone(), source)` [2](#0-1) . Deserialization of `Message` happens inside the Axum `Json` extractor via `serde_json`, prior to `process()`/`process_request_internal()` even being invoked.
- `Message` uses `#[serde(untagged)]` together with `WireMessage`/`Broken(Value)`, meaning `serde_json` first attempts to parse the payload into a generic `serde_json::Value` tree, walking the full nested structure recursively [3](#0-2) .
- `Request.params`/`Response.result`/`id` are plain `serde_json::Value`, so nesting depth is entirely attacker-controlled and unbounded [1](#0-0) .
- The only protection mentioned for the JSON-RPC endpoint is a flat request body size limit (documented as 10MB) [4](#0-3) ; this bounds total bytes but not nesting depth. A 10MB payload can encode millions of nesting levels (e.g. repeating `[` characters), which is more than enough to exhaust the parser's call stack.

Because recursion happens during deserialization of the request body itself — before `process_request_internal()`, before method dispatch, before any access-key/signature checks — this path is reachable by *any* unauthenticated network caller who can reach the RPC port (default `:3030`, route `/`), not just an authenticated transaction signer.

### Impact Explanation
A crafted deeply-nested JSON body sent to the `/` JSON-RPC endpoint can trigger a stack overflow in the `serde_json`/Axum `Json` extractor during request deserialization. In Rust, a stack overflow triggers process abort (SIGSEGV/SIGABRT), which crashes the entire node process — not just the individual HTTP request. This is a transaction/request-triggered halt of node availability: a single external caller can repeatedly crash validator or RPC nodes that expose the JSON-RPC interface, degrading or halting service. This matches the "transaction-triggered halt" / DoS impact class permitted by the scope rules.

### Likelihood Explanation
Likelihood is high for any node with `chain/jsonrpc` enabled and network-reachable (the default configuration for RPC and most validator nodes serving `:3030`). No authentication, valid transaction, or special account state is required — only an HTTP POST to `/` with a deeply nested JSON body. The existing 10MB body-size limit does not mitigate this because nesting depth scales with a fixed small per-level byte cost (1 byte per `[`), so millions of nesting levels fit well within the size limit.

### Recommendation
- Enforce a maximum JSON nesting depth before/while deserializing RPC bodies, e.g. by using a bounded/iterative JSON parser or `serde_json`'s recursion-limit-aware deserializer settings, or by pre-scanning the raw bytes for a hard depth cap and rejecting the request with a parse error before invoking `serde_json::from_slice`/the `Json<Message>` extractor.
- Alternatively, replace the default Axum `Json` extractor for this route with a custom extractor that runs deserialization in a worker with a limited stack and depth budget, converting any depth-limit violation into a normal 400 `RpcError::parse_error` response instead of allowing unbounded recursion.
- Apply the same depth guard to the Rosetta RPC server, which also deserializes client-controlled JSON payloads through `serde`.

### Proof of Concept
```
POST / HTTP/1.1
Host: <node-rpc-host>:3030
Content-Type: application/json
Content-Length: <N>

[[[[[[[[[[[[[[[[[[[[ ... (repeated several million times, well under 10MB) ... ]]]]]]]]]]]]]]]]]]]]
```
Sending this body to the node's JSON-RPC endpoint causes the Axum `Json<Message>` extractor to invoke `serde_json` deserialization, which recurses once per `[` while building the intermediate `Value` tree (via the `WireMessage`/`Broken(Value)` untagged enum), overflowing the stack and crashing the node process before any RPC method logic or transaction/account validation is reached.

### Citations

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

**File:** chain/jsonrpc-primitives/src/message.rs (L213-246)
```rust
///
/// Protocol-level errors.
#[derive(Debug, Clone, PartialEq, serde::Deserialize)]
#[serde(untagged)]
pub enum Broken {
    /// It was valid JSON, but doesn't match the form of a JSONRPC 2.0 message.
    Unmatched(Value),
    /// Invalid JSON.
    #[serde(skip_deserializing)]
    SyntaxError(String),
}

impl Broken {
    /// Generate an appropriate error message.
    ///
    /// The error message for these things are specified in the RFC, so this just creates an error
    /// with the right values.
    pub fn reply(&self) -> Message {
        match *self {
            Broken::Unmatched(_) => Message::error(RpcError::parse_error(
                "JSON RPC Request format was expected".to_owned(),
            )),
            Broken::SyntaxError(ref e) => Message::error(RpcError::parse_error(e.clone())),
        }
    }
}

/// A trick to easily deserialize and detect valid JSON, but invalid Message.
#[derive(serde::Deserialize)]
#[serde(untagged)]
pub enum WireMessage {
    Message(Message),
    Broken(Broken),
}
```

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

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```
