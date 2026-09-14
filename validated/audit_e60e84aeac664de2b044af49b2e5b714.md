## Analog vulnerability assessment: node-forge ASN.1 unbounded recursion → nearcore JSON-RPC message parsing

### Title
Unbounded recursive JSON parsing in JSON-RPC request deserialization causes stack-exhaustion crash - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The upstream advisory describes an uncontrolled-recursion (CWE-674) DoS where `node-forge`'s `asn1.fromDer` recurses once per nested constructed TLV with no depth cap, letting a small crafted blob exhaust the call stack. The closest reachable analog in nearcore is the JSON-RPC ingestion path: incoming request bodies are parsed via `serde_json`'s recursive-descent deserializer into the `Message`/`Request`/`Value` types with no depth limit enforced anywhere in the parsing pipeline.

### Finding Description
Every JSON-RPC POST to the node's `/` endpoint is deserialized with `near_jsonrpc_primitives::message::from_slice`, which calls `serde_json::de::from_slice` directly into the `WireMessage`/`Message` enum (which embeds `serde_json::Value` for `params`/`id`): [1](#0-0) 
The `Request`/`Notification` structs hold arbitrary, unbounded `serde_json::Value` params: [2](#0-1) 
Downstream, method-specific parameter parsing (`Params::parse`) again calls `serde_json::from_value` on the already-parsed `Value` tree, but the recursive descent that matters for stack depth already happens at the initial `from_slice`/`Value` construction stage: [3](#0-2) 

`serde_json`'s deserializer (both the `Deserializer` used for typed structs and the one used to build `Value`) recurses once per nesting level of a JSON array/object — there is no recursion-depth guard configured anywhere in this pipeline. Only a request **body size** limit is documented (`RequestBodyLimitLayer`, default 10 MB per `RPC_ARCHITECTURE.md`), not a **nesting-depth** limit: [4](#0-3) 
A 10 MB body can trivially encode millions of levels of `[[[[…]]]]` nesting (2 bytes per level), which is enough to blow the OS thread stack during recursive descent parsing, well before the semantic type-level protections that nearcore already has elsewhere (e.g., the deliberate `NonDelegateAction` guard against nested `DelegateAction` in `core/primitives/src/action/delegate.rs`, or the `MAX_DEPTH` guard on `ReceiptToTx` resolution) come into play — those protections only cover *domain* recursion (delegate actions, receipt chains), not the underlying wire-format JSON parser itself.

Unlike a `panic!`, Rust does not turn a native stack overflow into a catchable `Result`/`panic` — it triggers SIGSEGV/`abort()`, killing the entire process. Because the JSON-RPC server (`chain/jsonrpc`) runs in-process with the rest of the node (`ClientActor`, `RpcHandlerActor`, etc., per the same `RPC_ARCHITECTURE.md`), a crafted request that crashes the parsing thread's stack takes down the whole node process — not just the RPC handler.

### Impact Explanation
Any unauthenticated caller who can reach a node's public JSON-RPC port (mainnet/testnet RPC nodes, and validators that also expose RPC) can send a single, small, deeply-nested JSON payload to `/` and crash the entire nearcore process via stack exhaustion. This is a transaction/RPC-triggered halt of the affected node: for RPC-serving nodes this is an availability outage; if the same crash logic executes on a validator process that also runs an RPC endpoint, it can knock a validator offline. This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
High likelihood of reachability: the JSON-RPC `/` endpoint is a standard, always-on, unauthenticated entry point on every nearcore node (mainnet/testnet defaults documented in `RPC_ARCHITECTURE.md`). Crafting a deeply nested JSON array is trivial and requires no protocol knowledge, cryptographic material, or special privileges — only network access to the RPC port. The only existing mitigation is a body-size cap, which does not meaningfully limit nesting depth.

### Recommendation
- Enforce an explicit recursion/nesting-depth limit before/while parsing untrusted JSON-RPC bodies (e.g., wrap `serde_json::Deserializer` with a depth-limiting reader/visitor, or pre-scan bracket/brace nesting depth and reject payloads exceeding a small bound, e.g., 64–128 levels) in `near_jsonrpc_primitives::message::from_slice`.
- Apply the same nesting-depth guard to any other trust boundary parsing raw JSON via `serde_json` from network input (e.g., Rosetta RPC's `Json<...>` extractors in `chain/rosetta-rpc`).
- Consider running the HTTP request-handling worker on a thread with a guarded/larger stack plus `stacker`-style depth checks, or catching stack overflow via `sigaltstack`-based guards, as defense in depth.

### Proof of Concept
Conceptual PoC (not executed, since this environment is read-only):
```
POST / HTTP/1.1
Host: <node>:3030
Content-Type: application/json
Content-Length: <~5-10MB>

{"jsonrpc":"2.0","id":1,"method":"query","params":[[[[[[[[[[ ... (millions of nested arrays) ... ]]]]]]]]]]}
```
Sending this to a node's JSON-RPC port causes `near_jsonrpc_primitives::message::from_slice` (`serde_json::de::from_slice`) to recurse once per `[` encountered while building the `Value` tree for `params`, exhausting the OS stack and aborting the process before any application-level validation (`Params::parse`, `RpcRequest::parse`, method dispatch) ever runs.

---
Note on confidence: I was unable to fully trace the exact axum route handler function (`rpc_handler`) end-to-end in this pass (I confirmed the `Message`/`Request`/`Params` deserialization code paths and the documented architecture, but did not view the literal `Json<Message>` extractor call site in `chain/jsonrpc/src/lib.rs`). The core claim — that `serde_json`-based parsing of unbounded, attacker-controlled JSON without a depth guard sits directly on the JSON-RPC request path — is supported by the cited code. If you need the exact axum wiring confirmed, a deeper look at `chain/jsonrpc/src/lib.rs` (search for `Json<Value>`/`Json<Message>` and the route registered at `"/"`) or a Devin session with full repo access would close that gap.

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

**File:** chain/jsonrpc-primitives/src/message.rs (L259-264)
```rust
/// Read a [Message](enum.Message.html) from a slice.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}
```

**File:** chain/jsonrpc/src/api/mod.rs (L150-156)
```rust
        pub fn parse(value: Value) -> Result<T, RpcParseError>
        where
            T: DeserializeOwned,
        {
            serde_json::from_value(value)
                .map_err(|e| RpcParseError(format!("Failed parsing args: {e}")))
        }
```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```
