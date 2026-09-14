### Title
Uncontrolled JSON parsing recursion in the JSON-RPC request handler leads to stack-overflow process abort (Denial of Service) - ([File: chain/jsonrpc/src/lib.rs])

### Summary
The NEAR JSON-RPC server deserializes every inbound POST body directly into `near_jsonrpc_primitives::message::Message` via Axum's `Json<Message>` extractor before any application-level validation runs. `Message::Request` embeds an untyped `serde_json::Value` for `params` [1](#0-0) , and `serde_json::Value`'s `Deserialize` implementation is a classic unbounded recursive-descent parser: each nested JSON array/object level adds a stack frame, with no depth limit enforced anywhere in the pipeline before or during this deserialization. This is the same bug class as the reported Scriban issue (CWE-674, uncontrolled recursion in a parser with no default expression/nesting depth limit), but reachable in nearcore through an unauthenticated JSON-RPC POST request instead of a template string.

### Finding Description
The request pipeline is:
1. `rpc_handler()` receives `Json<Message>` — Axum invokes `serde_json` to deserialize the raw HTTP body into `Message` before any of `JsonRpcHandler::process()`'s logic executes [2](#0-1) .
2. `Message`/`Request`'s `params` field is a plain `serde_json::Value` with no `#[serde(deserialize_with = ...)]` guard and no recursion-limit wrapper [1](#0-0) ; `Message::from_slice` / `from_str` simply call `serde_json::de::from_slice` [3](#0-2) .
3. There is no size or depth guard configured on the route: only CORS and a documented flat request-body size limit (10 MB) are mentioned as middleware in the architecture doc [4](#0-3) ; there is no equivalent bound on JSON nesting depth. A 10 MB body is more than enough to encode millions of nested `[` characters (e.g. `"params":` followed by `[[[[[...]]]]]`), which is sufficient to exhaust a normal 2–8 MB OS thread stack during recursive descent of `serde_json`'s `Value` deserializer.
4. Because this happens inside `serde_json`'s deserialization machinery (called transitively through many stack frames of generic/monomorphized code), a stack overflow here is not a `Result::Err` — in Rust this manifests as a SIGSEGV/stack-overflow abort of the whole process, exactly analogous to the .NET `StackOverflowException` described in the report: it cannot be caught by any `catch_unwind`/`try`/`Result` handling and terminates the entire process, including any co-located validator/client actors running in the same `neard` binary.
5. This is reachable pre-authentication and pre-validation by any RPC caller: no signature, account, or stake is required — it is exactly the "RPC caller" class explicitly listed as an in-scope reachable path.

This mirrors the same root cause pattern the codebase is otherwise careful about elsewhere (e.g., `print_recursive_internal`'s explicit `max_depth`/`limit` parameters [5](#0-4) , and `get_subtree_size`'s comment "Non recursive approach to avoid any potential stack overflows" [6](#0-5) ) — but the JSON-RPC ingestion path itself has no such bound, because the recursion happens inside the third-party `serde_json` crate before any nearcore code (including these depth-limited routines) ever runs.

### Impact Explanation
A single unauthenticated HTTP POST to the JSON-RPC endpoint (`/`) can crash the `neard` process hosting the RPC server. If the crashing node also participates in consensus/validation (common for smaller or self-hosted validator setups that expose RPC on the same binary/process), this is a remotely triggerable, unauthenticated denial-of-service that halts block production/participation for that node until manual restart. Even for RPC-only nodes, this takes down public RPC availability for all API consumers with a single crafted request, and the failure is not recoverable without an external process supervisor restarting `neard`.

### Likelihood Explanation
High. The attack requires no account, no gas, no stake, and no special network access — just an HTTP client capable of sending a POST request within the existing 10 MB body-size allowance. Crafting a deeply nested JSON array (e.g., ~2,000,000 levels of `[` within 10 MB) is trivial and deterministic; it does not depend on chain state, timing, or race conditions.

### Recommendation
- Enforce an explicit maximum JSON nesting depth before or during deserialization of the RPC request body (e.g., use a bounded/streaming JSON parser configuration, or a pre-pass that rejects payloads whose bracket/brace nesting exceeds a small limit such as 64–128, before handing the body to `serde_json`).
- Alternatively/additionally, run the initial `Json<Message>` deserialization on a dedicated worker with a bounded/guarded stack (e.g., via `stacker`/spawning with an explicit larger-but-still-bounded stack and a depth counter), so a stack exhaustion can be turned into a graceful error rather than a process abort.
- Apply the same fix to any other entry point that deserializes untrusted `serde_json::Value`/nested JSON without a depth bound (e.g., Rosetta RPC in `chain/rosetta-rpc`).
- Add a regression test that POSTs a deeply nested JSON body and asserts the server returns a JSON-RPC parse error rather than crashing.

### Proof of Concept
```
POST / HTTP/1.1
Host: <node>:3030
Content-Type: application/json
Content-Length: <size>

{"jsonrpc":"2.0","id":"1","method":"status","params":[[[[[[[[[[ ... repeated ~2,000,000 times ... ]]]]]]]]]]}
```
Sending this body (kept under the documented 10 MB request-size limit) to the node's JSON-RPC endpoint causes `Json<Message>` extraction in `rpc_handler()` to recursively descend through `serde_json`'s `Value` deserializer for each nesting level, exhausting the calling thread's stack and aborting the `neard` process before any application code (`JsonRpcHandler::process`) is reached.

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

**File:** chain/jsonrpc-primitives/src/message.rs (L259-271)
```rust
/// Read a [Message](enum.Message.html) from a slice.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}

/// Read a [Message](enum.Message.html) from a string.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_str(s: &str) -> Parsed {
    from_slice(s.as_bytes())
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

**File:** core/store/src/trie/mod.rs (L1057-1061)
```rust
    ) -> std::io::Result<()> {
        if max_depth == 0 || *limit == 0 {
            return Ok(());
        }
        *limit -= 1;
```

**File:** core/store/src/trie/trie_recording.rs (L302-304)
```rust
        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);
```
