### Title
Unbounded JSON Nesting Depth Causes Stack Overflow / Process Crash in JSON-RPC Message Parsing - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The NEAR JSON-RPC server deserializes every incoming HTTP request body directly into the `Message` enum via `axum::extract::Json<Message>`, which is backed by `serde_json`'s recursive-descent parser [1](#0-0) . `Message` is an untagged enum whose `Request`/`Notification` variants hold an arbitrary `serde_json::Value` for `params` [2](#0-1) [3](#0-2) . `serde_json::Value`/`Deserializer` recursion depth is not bounded (no `disable_recursion_limit`/`serde_stacker` guard is used anywhere in the RPC crate), so a small, deeply nested JSON payload (e.g. `params` consisting of tens of thousands of nested arrays `[[[[...]]]]`) drives unbounded recursive calls in the parser, exhausting the stack and crashing the worker thread/process — the same bug class as CVE-2022-40149 (Jettison stack-overflow DoS via untrusted nested input).

### Finding Description
The only guard configured on the RPC HTTP endpoint is a request-body size limit (`DefaultBodyLimit`, default 10MB per `RPC_ARCHITECTURE.md`) [4](#0-3) ; there is no limit on JSON nesting depth. A payload with deep bracket nesting can be only a few hundred KB yet contain hundreds of thousands of nested container tokens, well under the size limit. When `rpc_handler` extracts `Json<Message>` from the body [5](#0-4) , axum internally calls `serde_json::from_slice`, which recurses once per nesting level while parsing arrays/objects into the `Value` tree backing `Request::params` / `Notification::params` [6](#0-5) . Because `Message` is `#[serde(untagged)]` [7](#0-6) , serde_json additionally has to attempt multiple deserialization passes over the same nested value (buffering it as `serde_json::Value` internally to try each variant), which does not reduce the native-stack recursion problem and can amplify parse cost. Since native stack overflow in Rust generally aborts the process rather than unwinding cleanly, a single unauthenticated HTTP POST to the public JSON-RPC endpoint (`/`, default port 3030) can crash the `neard` process handling that request, taking down the RPC service (and, depending on process/task architecture, other in-process actors).

### Impact Explanation
This is a transaction/RPC-caller-reachable, no-privilege-required Denial of Service: any anonymous JSON-RPC caller can submit a single crafted request that crashes the RPC-serving process. Because the same Axum server is what routes to `ClientActor`, `ViewClientActor`, and `RpcHandlerActor`, a crash disrupts node availability for the operator running that endpoint. This matches the "transaction-triggered halt" / DoS class explicitly allowed by the validation rules, and is analogous in root cause (unbounded recursive parser on untrusted nested input) to CVE-2022-40149.

### Likelihood Explanation
High likelihood of reachability: the JSON-RPC endpoint is intended to accept arbitrary external JSON-RPC 2.0 requests (`method`, `params`) from any caller with no authentication requirement described in the routing code [8](#0-7) , and the only mitigation present is a byte-size limit, not a structural depth limit, so crafting a payload that stays under 10MB while nesting deeply is straightforward.

### Recommendation
- Configure `serde_json` to use a bounded recursion strategy for RPC message deserialization — e.g., wrap the deserializer with `serde_stacker::maybe_grow` around the `Json<Message>` extraction path, or implement/enforce an explicit maximum JSON nesting depth check before/while parsing `Request`/`Notification::params` in `chain/jsonrpc-primitives/src/message.rs`.
- Alternatively, replace the default Axum `Json` extractor for this route with a custom extractor that pre-validates nesting depth (walking brackets without full parse) before invoking `serde_json::from_slice`.
- Add a regression test posting a deeply nested (e.g., 100k+ levels) but small JSON body to the `/` RPC endpoint and asserting the server returns a JSON parse error/HTTP 400 rather than crashing.

### Proof of Concept
1. Start `neard` with the default JSON-RPC server enabled (port 3030).
2. Send a POST request to `http://<node>:3030/` with `Content-Type: application/json` and a body such as:
   ```
   {"jsonrpc":"2.0","method":"query","id":1,"params": <N nested arrays, e.g. "[" * 200000 + "]" * 200000>}
   ```
   This body is well under the 10MB body-size cap.
3. Axum's `Json<Message>` extractor invokes `serde_json` to parse the body into `Message`/`Value` [1](#0-0) ; the recursive-descent parser recurses once per `[`, exhausting the thread stack and aborting the process handling the request.
4. Observed result: the `neard` process (or its RPC-handling task/thread) crashes, denying service to all RPC clients relying on that node.

*(Note: I was not able to execute this PoC in a live environment; the analysis is based on static code review of the parsing path and known `serde_json` recursion behavior. Exact crash behavior — full process abort vs. isolated task panic — depends on the Tokio/Axum runtime configuration, which I could not fully verify from the indexed code alone.)*

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

**File:** chain/jsonrpc-primitives/src/message.rs (L269-271)
```rust
pub fn from_str(s: &str) -> Parsed {
    from_slice(s.as_bytes())
}
```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L103-114)
```markdown
When a POST request arrives at `/`:

1. **`rpc_handler()`** - Deserializes the body into a JSON-RPC `Message`. Calls `JsonRpcHandler::process()`.
2. **`process()`** - Validates it's a `Request`, extracts `id`, delegates to `process_request()`.
3. **`process_request()`** - Metrics wrapper (timing, request count, error count per method). Delegates to `process_request_internal()`.
4. **`process_request_internal()`** - Core routing. Tries in order:
   - Adversarial requests (only with `test_features` cargo feature).
   - `process_basic_requests_internal()` - matches method name against known RPC methods.
   - Special `"query"` branch with sub-type metrics tracking.
   - Returns `method_not_found` if no match.
5. **`process_method_call()`** - Generic helper: parses params via `R::parse()`, invokes handler, serializes result, converts errors.
6. **HTTP status code mapping** in `rpc_handler()`: 200 (success), 400 (validation), 408 (timeout), 422 (UNKNOWN_BLOCK behind head), 500 (internal).
```
