### Title
Uncontrolled recursive JSON deserialization in JSON-RPC message parsing allows unauthenticated remote crash of the node process - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The NEAR JSON-RPC endpoint deserializes every incoming HTTP POST body directly into the `Message` enum via `axum::extract::Json<Message>` in the `rpc_handler` handler [1](#0-0) . `Message` is an untagged enum whose variants (`Request`, `Response`, `Notification`, `Batch(Vec<Message>)`) carry a free-form `serde_json::Value` for `params`/`result`, and `Batch` itself is a recursive `Vec<Message>` [2](#0-1) . Deserializing arbitrary JSON into `serde_json::Value` (and into nested `Message`/`Batch` structures) is implemented recursively by `serde_json`, with no depth limit enforced anywhere in this parsing path. This mirrors exactly the LlamaIndex `JSONReader` bug class (CWE-674): an untrusted, deeply-nested JSON payload drives an unbounded recursive descent parser, exhausting the call stack.

### Finding Description
The only mitigation on the request body is a size cap; the RPC architecture notes describe a "request body size limit (default 10MB)" middleware [3](#0-2) , but there is no JSON nesting-depth limit. A 10 MB payload can encode millions of nesting levels (e.g. `[[[[...]]]]`), which is far beyond what a native thread stack (typically 2–8 MB) can accommodate for a recursive descent parser. `serde_json`'s `Value` deserializer and the derived `Deserialize` implementations for `Message`/`Notification`/`Response` recurse once per JSON nesting level with no explicit recursion-depth counter [2](#0-1) . When the nesting exceeds the available stack, Rust triggers a stack overflow, which — unlike a catchable `panic!` — immediately aborts the process (SIGSEGV/guard-page violation), bypassing panic handlers, `catch_unwind`, and async task isolation.

Because this happens during body deserialization inside the Axum `Json<Message>` extractor — i.e., before `JsonRpcHandler::process()` or any application-level validation runs — no authentication, valid method name, or well-formed RPC semantics are required. Any anonymous caller reaching the `/` POST route can trigger it.

### Impact Explanation
A stack overflow inside the request-handling task aborts the entire node process, not just the offending connection/task. This is a whole-process crash, taking down `neard` (validator, RPC, or otherwise) with a single unauthenticated HTTP request. For a validator node this directly disrupts block production/participation (availability), and for a general RPC node it denies service to all other JSON-RPC/view-call clients. This matches the CVSS `AV:N/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H` profile of the source advisory: no confidentiality/integrity impact, but complete availability impact via a network-reachable, low-complexity, no-privilege request.

### Likelihood Explanation
High. The attack requires only a single HTTP POST to the public JSON-RPC endpoint with a deeply nested JSON body (e.g., a long chain of nested arrays as `params`, or nested `Batch` entries) — well within the 10 MB body size limit. No wallet, staking, or contract-deployment privileges are needed; the JSON-RPC endpoint is explicitly listed as an in-scope, unprivileged-caller-reachable surface.

### Recommendation
- Impose an explicit recursion/nesting-depth limit before or during JSON deserialization of RPC request bodies (e.g., use a bounded/iterative JSON parser, or a depth-tracking `Deserializer` wrapper such as `serde_stacker`, or reject payloads whose nesting exceeds a small fixed depth, e.g., 64–128 levels).
- Apply the same depth limiting to `Batch` handling in `chain/jsonrpc-primitives/src/message.rs`, since `Batch(Vec<Message>)` can also recurse through nested `Message`s.
- Consider running request deserialization on a dedicated thread with a guarded, generously sized stack, or use `serde_json`'s streaming/iterative value parsing where depth can be checked incrementally, so a stack overflow becomes a graceful parse error instead of a process abort.
- Reduce/align the default body size limit with the chosen depth limit so an attacker cannot rely on the size margin to build sufficiently deep nesting.

### Proof of Concept
1. Start a node with the JSON-RPC server enabled (default configuration, port 3030).
2. Send a POST request to `/` with a JSON body consisting of a JSON-RPC request whose `params` field is nested arrays sufficiently deep to exceed the runtime thread's stack size, e.g. (conceptually):
   ```
   {"jsonrpc":"2.0","method":"block","id":1,"params": [[[[[ ... deeply nested ... ]]]]]}
   ```
   generated programmatically with, e.g., 2,000,000 levels of nested `[` characters (well under the 10 MB body limit), followed by matching closing brackets and the remaining JSON-RPC fields.
3. Axum's `Json<Message>` extractor invokes `serde_json` to deserialize the body into `Message` before any handler logic runs [1](#0-0) .
4. The recursive descent through nested `Value`/array elements exhausts the stack, causing the OS to deliver a stack-overflow fault; the Rust runtime aborts the process rather than unwinding, crashing the entire `neard` process and terminating service for all users of that node.

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

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```
