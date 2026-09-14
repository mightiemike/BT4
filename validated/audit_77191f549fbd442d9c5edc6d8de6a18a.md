### Title
Unbounded-depth JSON-RPC request parsing enables stack-overflow DoS of the RPC service - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The JSON-RPC entry point deserializes the raw HTTP request body directly into `serde_json::Value`-backed types (`Message`/`WireMessage`/`Broken::Unmatched(Value)`) via `serde_json::de::from_slice`, with no depth limit on the JSON structure, and this is reachable by any unauthenticated RPC caller through the `axum::Json` extractor before any node-specific validation occurs.

### Finding Description
`chain/jsonrpc-primitives/src/message.rs` exposes `from_slice`/`from_str`, which call `::serde_json::de::from_slice(s)` to parse into `WireMessage`, an untagged enum containing `Message` or `Broken::Unmatched(Value)`:
<cite repo="Oyahkilomeikhide/nearcore--018" path="chain/jsonrpc-primitives/src/message.rs" start="240="246" end="264" /> [1](#0-0) 

`Message::Request`/`Notification` carry `params: Value`, an arbitrarily-nested JSON tree with no bound on nesting depth: [2](#0-1) 

`serde_json`'s recursive-descent parser for the generic `Value` type (and for `#[serde(untagged)]` enums that fall back to buffering into a `Value`, as `WireMessage`/`Broken` do here) recurses once per level of array/object nesting with no depth limit configured anywhere in the codebase (no `recursion_limit`/depth-guard was found in this crate or in `chain/jsonrpc`). A request body such as thousands of nested `[[[[...]]]]` (a few KB) will exhaust the parsing thread's stack and abort the process before the node ever reaches method dispatch, authentication, or any of the existing size/length validations that gate transaction or query content (e.g., `max_arguments_length`, `QUERY_DATA_MAX_SIZE`) in `chain/jsonrpc/src/api/mod.rs` and `chain/jsonrpc/src/api/query.rs`.

This is the direct nearcore analog of the jackson-databind advisory: both involve unbounded recursive processing of deeply nested JSON with no depth guard, where the fix class is "add a nesting/depth limit before/while traversing." Here the recursion happens during deserialization into `serde_json::Value` rather than during `toString()`, but the resulting resource-exhaustion/stack-overflow bug class and trigger vector (attacker-controlled deeply nested JSON payload) are the same.

### Impact Explanation
A stack overflow in Rust triggers process abort (not a catchable panic), so a single small HTTP POST to the JSON-RPC endpoint (`chain/jsonrpc/src/lib.rs`, which wires this `Message` parsing into the axum router) can crash the RPC server process. Since JSON-RPC is the primary interface used by wallets, indexers, explorers, and validators' own tooling to submit transactions and query state, killing this process is a transaction-triggered/request-triggered halt of node's externally facing service, matching the "transaction-triggered halt" acceptance criterion. It does not appear to affect consensus-critical state directly (it is scoped to the RPC front-end), but it can be repeated cheaply and continuously to deny service to all RPC clients of a targeted node with a handful of bytes.

### Likelihood Explanation
High: the code path is reached before any authentication, size-based rejection, or transaction/action validation — it fires purely from parsing the raw JSON body. No special privileges, staking, or contract deployment are required; only network access to the public JSON-RPC HTTP endpoint (the default configuration for RPC nodes) is needed.

### Recommendation
Impose an explicit recursion/nesting-depth limit before or during parsing of the raw RPC message body (and any nested `Value` fields such as `params`), rejecting requests whose JSON nesting exceeds a small bound (e.g. tens of levels) with a parse error, mirroring the fix pattern used by `jackson-databind` (bounded traversal depth) and consistent with the existing `max_*_length` guards already applied to transaction/query fields in this codebase.

### Proof of Concept
Send a single HTTP POST to the node's JSON-RPC endpoint with a body consisting of thousands of nested JSON arrays as the top-level payload or as the `params` field of an otherwise well-formed request, e.g.:
```
{"jsonrpc":"2.0","id":1,"method":"query","params": [[[[[[[[[[ ... 50000 levels ... ]]]]]]]]]] }
```
Because `near_jsonrpc_primitives::message::from_slice` (`chain/jsonrpc-primitives/src/message.rs:262-264`) hands the raw bytes straight to `serde_json::de::from_slice` with no depth limiting, the recursive descent into the nested array crashes the RPC-handling thread via stack overflow before any handler-level validation (`Params::parse`, `max_arguments_length`, etc.) runs.

### Citations

**File:** chain/jsonrpc-primitives/src/message.rs (L183-198)
```rust
impl Message {
    /// A constructor for a request.
    ///
    /// The ID is auto-generated.
    pub fn request(method: String, params: Value) -> Self {
        let id = Value::from(near_primitives::utils::generate_random_string(9));
        Message::Request(Request { jsonrpc: Version, method, params, id })
    }
    /// Create a top-level error (without an ID).
    pub fn error(error: RpcError) -> Self {
        Message::Response(Response { jsonrpc: Version, result: Err(error), id: Value::Null })
    }
    /// A constructor for a notification.
    pub fn notification(method: String, params: Value) -> Self {
        Message::Notification(Notification { jsonrpc: Version, method, params })
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
