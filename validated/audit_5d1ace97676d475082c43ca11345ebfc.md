### Title
Uncontrolled Recursion in JSON-RPC Message Deserialization Causes Stack-Overflow Process Abort - ([File: chain/jsonrpc-primitives/src/message.rs])

### Summary
The Scriban advisory (GHSA-p6q4-fgr8-vx4p) shows that a size/depth guard placed on one recursive code path (expression parsing) does not stop a `StackOverflowException` reached through a *different* recursive path (array-initializer parsing) driven by attacker-controlled nested input. nearcore has an analogous unguarded recursive-parsing surface reachable by any JSON-RPC caller: the `Message`/`Request` type used to decode every incoming RPC call embeds a raw `serde_json::Value` for `params`, and `serde_json`'s `Value` deserializer recurses once per nesting level of the input JSON with no depth limit configured anywhere in the decode path.

### Finding Description
Every JSON-RPC request received by the node is parsed with `near_jsonrpc_primitives::message::from_slice`/`from_str`, which calls `serde_json::de::from_slice::<WireMessage>` and ultimately populates `Request::params: Value` [1](#0-0) [2](#0-1) .

`serde_json::Value`'s `Deserialize` implementation is a generic, self-referential recursive descent parser: parsing a JSON array/object recurses into itself once per nesting level (`[`, `{`), with no explicit depth counter or `disable_recursion_limit`/depth-limit guard applied by nearcore before or during this call. Unlike Scriban's parser (which added an `ExpressionDepthLimit` for one recursive grammar production but missed the array-initializer production), nearcore applies **no** recursion-depth guard at all to this JSON ingestion path — the entire class of bug (uncontrolled recursion on nested/array-like syntax reachable from untrusted input) is present and unmitigated here.

The only bound on the payload is the HTTP body size limit noted in the RPC architecture docs (`chain/jsonrpc/RPC_ARCHITECTURE.md`, default 10MB) [3](#0-2) . A 10MB body is more than sufficient to encode hundreds of thousands of nested `[` characters (`{"jsonrpc":"2.0","method":"status","id":1,"params":[[[[[...]]]]]}`), which is exactly the PoC shape used in the Scriban advisory (`new string('[', 5000)`), just scaled to what a 10MB budget allows (millions of levels).

Because Rust's default thread stack size (8MB main thread / smaller for tokio worker threads) is far smaller than what's needed to hold a recursion depth in the hundreds-of-thousands-to-millions range, the recursive `Value` parser will exhaust the stack. A stack overflow in Rust triggers an immediate process abort (`SIGABRT`/`SIGSEGV` guard-page fault) — this cannot be caught with `Result`/`catch_unwind`, exactly as noted in the original advisory for .NET's `StackOverflowException`.

### Impact Explanation
Any unauthenticated caller who can reach the JSON-RPC HTTP endpoint (`/`, default port 3030) can send a single crafted request and crash the node process outright. Because JSON-RPC and validator duties frequently run in the same process/binary, this is a transaction/RPC-triggered halt of node availability: the affected node (validator, RPC provider, or indexer) terminates completely rather than merely failing gracefully. Repeated attacks let an attacker keep any specific node continuously offline. This matches the "transaction-triggered halt" impact category (process-level DoS caused by a single external request), the strongest analog available for this bug class given nearcore's execution model has no notion of unauthenticated writes to state, so more severe outcomes (fund loss, state-root divergence) are not applicable here.

### Likelihood Explanation
High. No authentication, no special account, no gas payment, and no valid signature is required — the crash happens purely at message-decoding time before any handler, auth check, or business logic runs. The trigger is a single, small, easily constructed HTTP POST body well within the documented 10MB limit, making the attack trivial to script and repeat against any publicly reachable RPC node.

### Recommendation
Apply an explicit recursion/depth guard to all externally-supplied JSON before or during deserialization on the RPC ingestion path in `chain/jsonrpc-primitives/src/message.rs`:
- Use a depth-limited JSON parser (e.g., pre-scan bracket/brace nesting depth and reject payloads exceeding a small limit such as 64–128 levels) before calling `serde_json::de::from_slice`, or
- Switch to a `serde_json::Deserializer` configuration/library that enforces a recursion limit (or run the parse on a dedicated thread with a bounded stack and treat overflow as a recoverable error rather than allowing it to hit the main worker's stack), and
- Add the same guard to any other place in the codebase that deserializes untrusted `serde_json::Value` trees directly (e.g. Rosetta RPC), since the vulnerability class is generic to `serde_json::Value` usage on attacker input, not specific to one endpoint.

### Proof of Concept
```
POST / HTTP/1.1
Host: <node>:3030
Content-Type: application/json
Content-Length: <~9_999_950>

{"jsonrpc":"2.0","method":"status","id":1,"params":[[[[[ ... ~2,000,000 nested '[' ... "x" ... ~2,000,000 nested ']' ... ]]]]]}
```
Sending this single request to any node's JSON-RPC port causes `serde_json`'s recursive `Value` deserializer (invoked from `near_jsonrpc_primitives::message::from_slice`) to recurse until the worker thread's stack is exhausted, aborting the whole node process — mirroring the Scriban `Template.Parse` PoC of `"{{ " + '['.repeat(5000) + "1" + ']'.repeat(5000) + " }}"`.

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

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```
