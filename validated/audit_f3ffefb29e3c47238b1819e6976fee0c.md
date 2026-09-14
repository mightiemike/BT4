## Title
Unbounded recursive-descent JSON parsing of RPC request bodies causes stack-overflow node crash - (File: chain/jsonrpc/src/lib.rs)

### Summary
The Redis/Valkey CVE is a denial-of-service caused by unbounded recursion when matching a very long, attacker-controlled pattern string, leading to a stack overflow and process crash. The nearcore JSON-RPC server has an analogous unbounded-recursion pattern: the request body is deserialized directly into a generic, recursively-defined `serde_json::Value` tree with no depth limit, driven entirely by attacker-controlled nesting depth of the JSON payload, before any semantic validation happens.

### Finding Description
Every JSON-RPC request hits `rpc_handler` in [1](#0-0) , which uses the Axum `Json<Message>` extractor to deserialize the HTTP body. `Message` is an untagged enum whose `Request`/`Notification` variants carry a `pub params: Value` field [2](#0-1) , and `Value` (`serde_json::Value`) is an arbitrarily-nested recursive data structure (`Array(Vec<Value>)`, `Object(Map<String, Value>)`).

`serde_json`'s default deserializer parses nested arrays/objects using recursive-descent — each nested `[` or `{` recurses one more stack frame — and by default has no configured recursion-depth limit in this code path. Constructing the `Value` tree from a deeply nested JSON body (e.g. `[[[[[...]]]]]` many tens of thousands of levels deep) will therefore recurse on the parser's call stack proportionally to the nesting depth supplied by the caller, exactly mirroring the Redis bug class of "matching of extremely long/attacker-chosen patterns causing unbounded recursion → stack overflow → process crash."

The only mitigating control I could confirm is a body-size limit layer referenced in `chain/jsonrpc/src/lib.rs` (grep shows `RequestBodyLimit`/`max_body` references), documented elsewhere as a default 10MB request body cap [3](#0-2) . A 10MB payload is far more than sufficient to encode hundreds of thousands of nesting levels of `[` characters (1 byte per nesting level), so the byte-size limit does not bound the *recursion depth* — it only bounds total payload size, not structural depth. There is no separate JSON nesting-depth limit visible in the RPC ingestion path (`chain/jsonrpc/src/lib.rs`, `chain/jsonrpc-primitives/src/message.rs`, `chain/jsonrpc/src/api/mod.rs`).

Because deserialization into `Value` happens in `rpc_handler`/`Message::deserialize` before `process_request_internal`/`process_basic_requests_internal` ever inspect `method` or route to a specific request type's `parse()` (which is where method-specific validation like `validate_view_state_pagination` in `chain/jsonrpc/src/api/query.rs` would run), the stack overflow occurs unconditionally for any POST body, regardless of the RPC `method` name or whether the caller is otherwise authorized to invoke any privileged method. Rust's default behavior on stack overflow (SIGSEGV/`abort`) crashes the whole process (a Rust panic cannot unwind through a stack overflow), taking down the RPC server (and, since `ClientActor`/`ViewClientActor` typically run in the same process as the JSON-RPC server, potentially the validator/node process itself).

### Impact Explanation
A single unauthenticated HTTP POST to the public JSON-RPC endpoint (default port 3030, exposed to "RPC caller" class explicitly in-scope) with a deeply nested JSON array can crash the node process via stack overflow before any application-level request validation or rate limiting is applied. This is a transaction-triggered/RPC-triggered halt of node operation — directly matching one of the "Accept only concrete ... transaction-triggered halt" criteria in the validation rules. If exploited against many/most RPC nodes simultaneously (a widely available, non-privileged attack), it can cause a network-wide availability incident for JSON-RPC access, and if `ClientActor` shares a process with `JsonRpcHandler` (default nearcore deployment), block production/participation for that node can also be disrupted.

### Likelihood Explanation
High. This requires only network access to any JSON-RPC endpoint and does not require holding an account, a valid access key, gas, or any signed transaction — it is one of the easiest primitives to trigger identified in the report's action list ("JSON-RPC ... RPC caller"). No special protocol knowledge is needed beyond crafting a deeply nested JSON document; standard JSON tooling can generate arbitrarily deep nesting trivially within the existing ~10MB body-size cap.

### Recommendation
- Impose an explicit maximum JSON nesting depth on inbound RPC request bodies, independent of total payload byte size, before/while parsing into `Value` (e.g., use a depth-limited/iterative JSON parser, or a `serde_json::Deserializer` wrapped with a recursion-depth guard, or reject requests whose raw body contains more than N consecutive `[`/`{` without matching closes via a cheap pre-scan).
- Alternatively/additionally, run the JSON body deserialization in a context with a bounded stack (e.g., a dedicated thread with a small, defined stack size) so a stack overflow triggers a bounded thread panic rather than crashing the entire process, and return a `400 Bad Request`/parse error to the caller instead.
- Add regression tests in `chain/jsonrpc/jsonrpc-tests/tests/` that POST deeply nested JSON payloads and assert the server returns a parse error rather than crashing.

### Proof of Concept
```
POST / HTTP/1.1
Host: <node-rpc-host>:3030
Content-Type: application/json
Content-Length: <size>

{"jsonrpc":"2.0","id":1,"method":"status","params": <N nested arrays, e.g. "[" * 200000 + "]" * 200000> }
```
Sending this body (well under the ~10MB body-size limit) to `rpc_handler`/`Json<Message>` in `chain/jsonrpc/src/lib.rs` triggers deep recursive-descent parsing of the `params` field into `serde_json::Value`, exhausting the thread stack and crashing the process before any RPC method dispatch or validation occurs.

**Uncertainty note:** I was not able to fully confirm within the indexed context (a) the exact configured request-body size limit value/middleware call site in `chain/jsonrpc/src/lib.rs` (only grep hits for `RequestBodyLimit`/`max_body`, not full surrounding code), and (b) whether any global `serde_json` recursion-limit feature or wrapper is configured elsewhere in the workspace `Cargo.toml`/build settings that might mitigate this. A Devin session with full repository/file access would be needed to verify the exact body-limit configuration and confirm no existing depth guard is present before treating this as fully confirmed.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L2928-2932)
```rust
async fn rpc_handler(
    State(handler): State<Arc<JsonRpcHandler>>,
    headers: axum::http::HeaderMap,
    Json(request): Json<Message>,
) -> Response {
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

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L95-95)
```markdown
Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).
```
