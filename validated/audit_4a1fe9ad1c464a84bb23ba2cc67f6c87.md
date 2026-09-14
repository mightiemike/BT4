### Title
Uncontrolled Recursion in JSON-RPC Message Deserialization Causes Node-Wide Denial of Service - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The nearcore JSON-RPC server accepts arbitrary caller-supplied JSON at `POST /` and deserializes it into the `Message` enum via `axum`'s `Json<Message>` extractor before any application-level validation happens. `Message` is a `#[serde(untagged)]` enum with a recursive `Batch(Vec<Message>)` variant, and several branches carry a raw `serde_json::Value` (e.g., `Notification.params`, `Request.params`). `serde_json`'s default deserializer is a recursive-descent parser with no configured depth limit, so parsing deeply nested JSON (arrays/objects/batches) recurses once per nesting level on the native call stack. This is the same bug class as SQUID-2024:1 (CVE-2024-25111): an uncontrolled-recursion decoder that a remote, unauthenticated (here: unprivileged) caller can trigger with a single crafted message to exhaust the stack and crash the process.

### Finding Description
The request pipeline is:
1. `rpc_handler()` in `chain/jsonrpc/src/lib.rs` (around lines 2928-2962) takes the `Json<Message>` extractor argument, meaning axum invokes `serde_json::from_slice`/`from_reader` to parse the body into `Message` before `rpc_handler` body code even runs. [1](#0-0) 
2. The only guard configured on this route is a byte-size cap: `RequestBodyLimitLayer::new(limits_config.json_payload_max_size)`, defaulting to 10 MiB. [2](#0-1) [3](#0-2) 
3. `Message` is defined as an untagged enum with a self-recursive `Batch(Vec<Message>)` variant and `Value`-typed fields (`params`) that themselves can nest arbitrarily: [4](#0-3) 
4. There is no recursion-depth guard anywhere in the codebase for this deserialization path — searches for `serde_stacker`, `disable_recursion_limit`, `RecursionLimit`, or any manual depth counter in the JSON-RPC crates return nothing. `serde_json`'s built-in "recursion limit" only protects `Deserializer::disable_recursion_limit`-style self-describing formats used for *typed* structs with a bounded static nesting (128 levels), but arbitrary `Value`/`Vec<Message>` parsing recurses with the raw JSON nesting depth, which is bounded only by the 10 MiB body-size limit, not by a fixed count. Since a single byte of nested-array syntax (`[`) contributes one stack frame, ~10 MiB of `[` characters can produce millions of nested-parse stack frames, far exceeding any reasonable native thread stack size (default 2–8 MiB for tokio worker threads) long before the byte limit stops it.
5. A Rust stack overflow is not a catchable exception: the language runtime installs a guard-page handler that prints "thread 'X' has overflowed its stack" and calls `abort()`, which terminates the entire process — not just the offending task/thread. Because the JSON-RPC server runs in the same process as `ClientActor`/`ViewClientActor`/the runtime, this kills the whole validating/RPC node.

### Impact Explanation
A single unauthenticated/unprivileged JSON-RPC caller (any external client that can reach the node's RPC port, which is the primary public interface for nodes and validators) can send one crafted HTTP POST request containing deeply nested JSON (well under the 10 MiB body limit) to `/rpc` and crash the entire `neard` process via stack exhaustion. This is a transaction/RPC-reachable, no-privilege-required denial of service against any node exposing its JSON-RPC endpoint (which includes validators that also run RPC, or nodes behind a load balancer serving public RPC). Repeated requests can be used to keep nodes down, degrading network availability and potentially validator participation.

### Likelihood Explanation
High likelihood: the JSON-RPC endpoint is a standard public-facing interface enabled by default (`rpc.enable_debug_rpc` not even required), the vulnerable code path (`Json<Message>` extraction) runs unconditionally on every request before any handler-level validation or authentication, and the request needed to trigger it is trivial to construct (a string of many `[` characters) and small enough to bypass the existing 10 MiB size guard.

### Recommendation
- Impose an explicit, low nesting-depth limit before/while deserializing untrusted JSON-RPC bodies — e.g. wrap `serde_json::Deserializer` with `serde_stacker`, or pre-scan/reject bodies whose bracket/brace nesting exceeds a small bound (e.g. 32–64) prior to full deserialization.
- Apply the same guard to any other externally-reachable `serde_json::Value`/recursive-enum deserialization paths (e.g. `EntityQueryWithParams` in `handle_entity_debug`, Rosetta RPC request bodies) since they share the same untrusted-JSON recursion pattern.
- Consider running request parsing inside a bounded worker with `stacker::maybe_grow` or a dedicated thread with a controlled stack size and graceful OOM/overflow handling, so a stack exhaustion cannot abort the whole process.

### Proof of Concept
1. Start a node with the JSON-RPC server enabled on the default port (3030).
2. Send a POST request to `/` with a body such as:
   `"[".repeat(2_000_000) + "]".repeat(2_000_000)` wrapped as the value of a JSON-RPC `params` field, e.g.:
   ```
   {"jsonrpc":"2.0","id":1,"method":"status","params": <2,000,000 nested arrays> }
   ```
   This body is well under the default 10 MiB `json_payload_max_size` limit.
3. Because `rpc_handler`'s `Json<Message>` extractor deserializes `params: Value` (and, for `Batch`, the recursive `Vec<Message>`) via `serde_json`'s recursive-descent parser with no depth cap, the parse recurses once per array nesting level.
4. The tokio worker thread handling the request overflows its stack; Rust's stack-overflow guard triggers `abort()`, terminating the whole `neard` process — a full node crash from a single unauthenticated RPC request.

(Note: I was not able to execute this PoC against a live build within this analysis; the finding is derived from static analysis of the request-parsing pipeline and the absence of any depth-limiting mechanism in the codebase. A background Devin session with terminal/build access would be needed to empirically confirm the exact required payload size and stack behavior on the target build configuration.)

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
