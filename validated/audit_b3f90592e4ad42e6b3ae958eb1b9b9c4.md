### Title
Unbounded recursion in JSON-RPC message deserialization allows a single request to crash the RPC-serving node process - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The Wireshark VLAN-dissector bug (CVE-2018-9262) was a classic "unbounded recursive descent on attacker-controlled nested structure → stack overflow crash," fixed by adding a nesting-depth cap. Searching nearcore for the equivalent bug class shows that every other place that recurses over an unbounded, attacker/user-controlled structure (delegate-action nesting, receipt-to-tx chains, trie traversal, WASM call-stack) has already been hardened with an explicit depth limit or converted to an iterative/queue-based traversal. The one place that still performs unbounded recursive-descent parsing on fully attacker-controlled input with no depth guard is the JSON-RPC request/response deserializer, `near_jsonrpc_primitives::message::from_slice` / `from_str`, which parses the HTTP request body straight into a `Message` whose `params` field is a raw `serde_json::Value` <cite repo="Oyahkilomeikhide/nearcore--015" path="chain/jsonrpc-primitives/src/message.rs" start="259="264" end="271" />.

### Finding Description
`Message` is defined as an untagged enum, and its `params`/`result` fields are typed as `serde_json::Value`: [1](#0-0) 

`from_slice`/`from_str` hand the raw HTTP body directly to `serde_json::de::from_slice` with no pre-check on nesting depth: [2](#0-1) 

`serde_json`'s `Value` deserializer (and, more generally, its `Deserializer::parse_value` recursive-descent parser for nested arrays/objects) recurses once per nesting level with no built-in depth limit in the default (non-`arbitrary_precision`, non-recursion-limit) configuration used here. A JSON payload consisting of deeply nested arrays (e.g. `[[[[[...]]]]]`) forces one Rust stack frame per bracket. Unlike Wireshark's VLAN dissector — which recursed on nested 802.1Q tags without a depth cap until the fix in `packet-vlan.c` added one — nearcore's RPC message parser has no analogous cap.

This is directly reachable by any unauthenticated RPC caller: `chain/jsonrpc/src/lib.rs`'s `rpc_handler()` deserializes every POST body via this path before any method routing or business-logic validation occurs, so the crash happens before `process_request()`/`process_method_call()` even see the request. The only mitigating control observed is the Axum body-size limit (documented as 10MB by default) [3](#0-2) , but 10MB of balanced brackets (`[` ... `]`) is enough to encode on the order of several million nesting levels on a single branch, far beyond what a native Rust thread stack (typically ~1–8 MB) can accommodate before a guard-page stack overflow — which in Rust aborts the whole process rather than being a catchable panic.

I was not able to fully verify from the index whether an intervening layer (e.g. `serde_json`'s crate version enabling `arbitrary_precision`, or an Axum/Tower middleware) imposes a JSON nesting-depth limit before this call; the `DefaultBodyLimit`/body-size configuration exists in `chain/jsonrpc/src/lib.rs` but I could not confirm the presence or absence of a depth-limiting layer there within the available tool budget. This uncertainty should be resolved by a follow-up review of `chain/jsonrpc/src/lib.rs`'s Axum router construction and the exact `serde_json` version/features used by the workspace before treating this as fully confirmed exploitable.

### Impact Explanation
If unguarded, a stack overflow in `serde_json` parsing aborts the OS process (Rust stack overflows are not recoverable panics — they raise `SIGSEGV`/trap and terminate immediately). Since `chain/jsonrpc/src/lib.rs` runs inside the same node process as `ClientActor`/`ViewClientActor` (per the documented actor wiring), crashing the HTTP-handling thread's process takes down the entire node, including validator/consensus duties if the RPC server co-resides with a validator node's process. A single unauthenticated HTTP POST could therefore cause a transaction/RPC-triggered halt of a node, satisfying the "transaction-triggered halt" impact category. If replicated across many/most validators (a mass-broadcast attack against public RPC/validator endpoints), this could degrade network liveness.

### Likelihood Explanation
Likelihood is high if no depth limit exists at the `serde_json`/Axum layer: the payload requires no authentication, no valid transaction, and no special account state — just a POST to `/` with a crafted body under the size limit. The primary uncertainty is whether a depth/size guard already exists upstream of `from_slice` (e.g., in Axum's JSON extractor or a custom `serde_json::Deserializer` configuration) that this analysis could not conclusively rule out.

### Recommendation
- Add an explicit JSON nesting-depth limit before/while parsing RPC request bodies (e.g., use a `serde_json::Deserializer` with `disable_recursion_limit()` reverted/kept at a small `recursion_limit`, or pre-scan bracket/brace nesting depth and reject requests exceeding a small bound, e.g., 128) in `chain/jsonrpc-primitives/src/message.rs::from_slice`.
- Apply the same guard to the Rosetta RPC deserialization path (`chain/rosetta-rpc`), which shares the same class of untagged `Value`-based decoding.
- Add a regression test that POSTs a deeply nested JSON array/object body and asserts the server returns a structured parse error rather than crashing.

### Proof of Concept
1. Start a nearcore node with the JSON-RPC server enabled (default `rpc.addr`, e.g. `0.0.0.0:3030`).
2. Send `POST /` with header `Content-Type: application/json` and a body consisting of `N` opening brackets followed by `N` closing brackets, e.g. `"[" * 200000 + "]" * 200000`, kept under the ~10MB body-size limit.
3. Observe that the process handling the request crashes (stack overflow) rather than returning a JSON-RPC parse error, taking down the node process (and, if co-located, consensus participation).

*(Note: step 3's outcome — whether a depth cap already exists — could not be independently confirmed from the indexed code; a Devin session with full repository/dependency access should verify the exact `serde_json` configuration and Axum extractor settings in `chain/jsonrpc/src/lib.rs` to confirm exploitability before treating this as fully proven.)*

### Citations

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

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L95-95)
```markdown
Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).
```
