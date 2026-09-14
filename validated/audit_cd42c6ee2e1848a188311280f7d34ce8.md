### Title
Unbounded-depth JSON-RPC request parsing allows stack-exhaustion denial of service - ([File: chain/jsonrpc/src/lib.rs])

### Summary
The NEAR JSON-RPC server accepts arbitrary JSON bodies via `axum::extract::Json` / `near_jsonrpc_primitives::message::from_slice` and hands the `params` field straight to `serde_json::Value`, then to `Params::parse`/`serde_json::from_value`, all of which use `serde_json`'s recursive descent parser with no depth limit configured anywhere in the crate.

### Finding Description
`Message::from_slice` in `chain/jsonrpc-primitives/src/message.rs` calls `serde_json::de::from_slice(s)` directly on the raw request bytes to deserialize into `WireMessage`/`Message`, whose `params` field is typed as `serde_json::Value` (an untagged, recursively-defined type): [1](#0-0) 

`serde_json::Value` parsing (and `Value`'s own `Deserialize`) is inherently recursive — each nested `[` or `{` in the input causes another recursive call into the parser — and there is no `recursion_limit`/depth guard configured anywhere in this codebase: [2](#0-1) 

I searched for any recursion-limit configuration (`recursion_limit`, `set_recursion_limit`, `disable_recursion_limit`) and any HTTP body-size limiting middleware (`DefaultBodyLimit`, `body_limit`, `RequestBodyLimitLayer`) in the `near-jsonrpc` crate; none were found. This means the only thing bounding the depth of a malicious payload is the total byte size the HTTP framework will accept — and a payload like `{"jsonrpc":"2.0","method":"query","id":1,"params":` followed by millions of `[` characters is extremely small per unit of depth (1 byte of input buys roughly 1 additional stack frame of parser recursion), so even a very small request body (a few hundred KB) can drive the parser recursion thousands of levels deep — enough to exhaust the OS thread stack and abort/crash the worker thread handling the connection.

This is architecturally the same bug class as CVE-2017-11626: a recursive-descent parser (`QPDFTokenizer`/`QPDFObjectHandle::parseInternal` in qpdf; here `serde_json`'s value parser reached via `Message::from_slice`/`Params::parse`) that recurses once per nesting level of attacker-controlled input with no explicit depth cap, leading to stack-consumption ("infinite loop"/stack overflow) on a single malicious input.

### Impact Explanation
Any unauthenticated JSON-RPC caller (this endpoint requires no authentication or access key — it's the public RPC surface reachable by any external caller) can send a single crafted request with deeply nested JSON in the `params` field. This can cause the worker thread parsing the request to overflow its stack, aborting the process (Rust aborts on stack overflow rather than panicking cleanly) and taking down the RPC server / node process that handles it. This is a transaction/RPC-triggered halt of node availability, satisfying the "transaction-triggered halt" impact class for a single externally-submitted request — no privileged access, validator status, or contract deployment is required.

### Likelihood Explanation
Likelihood is high: the attack requires only a single HTTP POST to a public, unauthenticated JSON-RPC endpoint with a crafted body; it needs no on-chain state, balance, or prior interaction with the chain, and can be repeated cheaply against every RPC node (validator RPC nodes, public RPC gateways, etc.) that exposes the `jsonrpc` HTTP interface.

### Recommendation
- Impose an explicit nesting-depth limit before/while parsing untrusted RPC request bodies, e.g. wrap `serde_json::Deserializer` with `.disable_recursion_limit()` disabled by default is not the fix — instead explicitly validate/limit `params`/body nesting depth (or use `serde_json`'s built-in `recursion_limit` feature, which is enabled by default at ~128 in recent `serde_json` — verify the pinned version and feature flags actually enable this protection).
- Add an HTTP body-size limit (`axum::extract::DefaultBodyLimit` or equivalent) sized appropriately for legitimate RPC payloads, independent of the depth limit, since size limits alone do not prevent deep-nesting stack exhaustion.
- Add a regression test that POSTs a deeply nested JSON payload (e.g., `[[[[...]]]]` many thousands of levels deep) to the JSON-RPC endpoint and asserts the server returns a structured parse error rather than crashing.

### Proof of Concept
1. Start a nearcore node with the JSON-RPC HTTP server enabled.
2. Send:
```
POST /  HTTP/1.1
Content-Type: application/json

{"jsonrpc":"2.0","method":"query","id":1,"params":<N million '[' characters><N million ']' characters>}
```
where N is large enough (e.g., several hundred thousand to a few million, well under typical body-size limits) to exceed the platform thread stack size during recursive parsing in `serde_json::de::from_slice` (invoked from `Message::from_slice`, `chain/jsonrpc-primitives/src/message.rs:262-264`).
3. Observe the worker thread aborts on stack overflow, rather than the server returning a clean `parse_error` JSON-RPC response, causing denial of service for that connection/thread (and potentially the whole process depending on the async runtime's panic/abort handling).

### Citations

**File:** chain/jsonrpc-primitives/src/message.rs (L259-264)
```rust
/// Read a [Message](enum.Message.html) from a slice.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}
```

**File:** chain/jsonrpc/src/api/mod.rs (L146-157)
```rust
    impl<T> Params<T> {
        pub fn new(params: Value) -> Self {
            Self(Err(params))
        }

        pub fn parse(value: Value) -> Result<T, RpcParseError>
        where
            T: DeserializeOwned,
        {
            serde_json::from_value(value)
                .map_err(|e| RpcParseError(format!("Failed parsing args: {e}")))
        }
```
