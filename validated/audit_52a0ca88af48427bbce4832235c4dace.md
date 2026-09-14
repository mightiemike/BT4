### Title
Unbounded JSON nesting depth in JSON-RPC request parsing causes recursive-descent stack overflow (process abort) - ([File: chain/jsonrpc-primitives/src/message.rs])

### Summary
The NEAR JSON-RPC server deserializes every inbound HTTP POST body with `serde_json`'s recursive-descent `Value`/`Message` deserializer, guarded only by a total request-body byte-size limit, not by a nesting-depth limit. An unauthenticated RPC caller can send a small, deeply-nested JSON payload (e.g., `[[[[...]]]]` repeated thousands of times) that stays well under the byte-size cap yet drives the parser's recursion past the process stack limit, producing an unrecoverable `SIGABRT` (Rust "stack overflow, aborting") and crashing the validator/RPC node process — the same bug class as the reported `ratex-parser` recursive-descent-with-no-depth-guard issue.

### Finding Description
Inbound JSON-RPC requests are parsed in `chain/jsonrpc-primitives/src/message.rs`: [1](#0-0) 
`from_slice`/`from_str` call `serde_json::de::from_slice` directly into the `WireMessage`/`Message` enum, whose `params`/`result` fields are typed as `serde_json::Value` — an untagged, fully recursive JSON value type: [2](#0-1) 
`serde_json`'s recursive descent visits one native stack frame per array/object nesting level when building a `Value`, with no built-in recursion-depth limit (unlike some codecs that impose a `recursion_limit` for typed deserialization). It is invoked here on completely untrusted, attacker-controlled bytes from the network before any application-level validation occurs.

The only mitigation in place is a byte-size cap on the whole HTTP body, applied via Axum's `RequestBodyLimitLayer`: [3](#0-2) 
This bounds total payload size (documented default of 10 MB per the RPC architecture notes) but does nothing to bound *nesting depth*. A payload consisting almost entirely of the single character `[` repeated N times, followed by `]` repeated N times, is only ~2×N bytes yet has nesting depth N — exactly the RaTeX PoC shape (`{`×200000 / `}`×200000, ~10 KB, causing an abort). A similarly-sized or even a few-hundred-KB JSON body with hundreds of thousands of nested arrays comfortably fits under a 10 MB body limit while still exceeding the thread's stack budget.

This mirrors the reported bug class precisely: a recursive-descent parser (`serde_json`'s `Value`/`Deserialize` implementation invoked transitively through `Message`/`Request`/`Notification`/`Response` deserialization) with **no maximum nesting-depth guard**, reachable from a single untrusted request, whose only existing protection (byte-size limit) is orthogonal to depth and does not prevent the overflow.

### Impact Explanation
A single POST to the public JSON-RPC endpoint (`/`, handled by `rpc_handler` per `create_jsonrpc_app` route wiring) can abort the entire `neard` process with `SIGABRT`, since Rust stack overflows are unconditionally fatal regardless of panic strategy. This is a reliable, unauthenticated, remote denial-of-service against any node exposing its JSON-RPC port (default 3030), including validator nodes that also run RPC, or nodes relied upon by other infrastructure (indexers, wallets, relayers). Because the same request-parsing path is shared by all JSON-RPC methods, no valid credentials or specific method knowledge are required — malformed nesting alone is sufficient to crash the listener before any method dispatch or transaction validation logic runs.

### Likelihood Explanation
High likelihood of triggerability: the vulnerable code path (`from_slice` → `serde_json::de::from_slice`) is on the default, always-reachable ingress for any JSON-RPC call, requires no authentication, and the PoC construction (a JSON array nested tens/hundreds of thousands of levels deep) is trivial to generate and stays within the documented body-size limit. The primary uncertainty is the exact stack size of the request-handling thread/task (Tokio worker threads typically default to several MB, main thread 8MB), which affects how deep the nesting must be to trigger the crash, but this only changes the required input size, not exploitability.

### Recommendation
- Impose an explicit maximum JSON nesting depth check before/while deserializing untrusted RPC bodies in `chain/jsonrpc-primitives/src/message.rs::from_slice`, e.g., by using a depth-limited JSON parser/visitor (or a pre-scan of bracket nesting depth) instead of directly handing attacker bytes to `serde_json::de::from_slice::<WireMessage>`.
- Alternatively/additionally, run the deserialization of untrusted RPC bodies on a dedicated thread with a bounded stack and treat overflow as a recoverable per-connection error rather than allowing it to abort the whole process, or use a `serde_json` feature/wrapper that enforces `recursion_limit` for arbitrary `Value` parsing.
- Apply the same nesting-depth guard to any other untyped `serde_json::Value` ingestion points reachable from RPC (e.g., legacy array-style `Params` parsing in `chain/jsonrpc/src/api/mod.rs`).

### Proof of Concept
Send a POST request to the JSON-RPC endpoint with a deeply nested `params` array that fits under `json_payload_max_size` but has extreme nesting depth, e.g. (conceptually, mirroring the RaTeX PoC):
```
python3 -c '
import sys
n = 200000
body = ("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"block\",\"params\":"
        + "["*n + "1" + "]"*n + "}")
sys.stdout.write(body)
' | curl -X POST http://<node>:3030/ -H "Content-Type: application/json" --data-binary @-
```
This body is a few hundred KB (well under the size limit) but nests `n` JSON arrays; parsing it via `serde_json::de::from_slice::<WireMessage>` in `from_slice` (`chain/jsonrpc-primitives/src/message.rs:262-263`) recurses `n` stack frames deep, expected to overflow the handling thread's stack and abort the `neard` process — analogous to the RaTeX `{`×200000/`}`×200000 PoC.

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

**File:** chain/jsonrpc/src/lib.rs (L3258-3260)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```
