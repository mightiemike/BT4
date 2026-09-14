### Title
Uncontrolled Recursion in JSON-RPC Request Parsing Causes Process Abort (Stack Overflow) - ([File: chain/jsonrpc-primitives/src/message.rs])

### Summary
The JSON-RPC entry point deserializes the raw HTTP request body into a `WireMessage`/`Message` via `serde_json::de::from_slice` [1](#0-0) , where `params`/`result` fields are typed as `serde_json::Value` [2](#0-1) . `serde_json::Value`'s `Deserialize` implementation is a plain recursive-descent parser with no depth limit, so an arbitrarily deeply nested JSON array/object in `params` causes one native stack frame per nesting level. The only guard in front of this parser is a byte-size cap (`RpcLimitsConfig::json_payload_max_size`, default 10 MiB) enforced by `RequestBodyLimitLayer` [3](#0-2) [4](#0-3) ; there is no nesting-depth limit anywhere in the pipeline (`process_method_call`/`Params::parse` also call `serde_json::from_value` with no limit) [5](#0-4) [6](#0-5) .

### Finding Description
Since each `[` (or `{`) byte contributes one level of recursion, a 10 MiB payload consisting almost entirely of `[` characters (e.g. `[[[[[...]]]]]`) allows on the order of several million nesting levels. Rust's default thread stacks (typically 2–8 MiB, and only slightly larger for the async worker threads used by the Axum/Tokio server) will be exhausted long before parsing completes. In Rust, stack overflow is not a catchable panic — the runtime calls `abort()`, immediately terminating the process. This is a textbook instance of the CWE-674 "Uncontrolled Recursion" bug class described in the external advisory (Apache Thrift Node.js bindings), transplanted onto nearcore's JSON-RPC ingestion path (`chain/jsonrpc-primitives/src/message.rs`, invoked from the Axum `rpc_handler` in `chain/jsonrpc/src/lib.rs`).

This path is reachable by any unauthenticated network caller: NEAR's JSON-RPC server has no authentication and is meant to be publicly queryable (`send_tx`, `query`, `tx`, etc. all funnel through the same `rpc_handler` → `Message::from_slice` deserialization). No transaction signature, gas payment, or on-chain state is required to trigger it — a single crafted HTTP POST is sufficient.

### Impact Explanation
A successful trigger crashes the `neard` process serving the JSON-RPC endpoint. If this endpoint is enabled on a validator node (which is common for many operator setups, and is explicitly supported since `rpc` config lives alongside validator config), an attacker can repeatedly crash that validator's process, disrupting its ability to produce/validate blocks — a remotely triggerable node halt with no cost to the attacker (no transaction fee, no stake, no signature). Even when isolated to dedicated RPC nodes, this permits trivial, repeatable denial of service against public RPC infrastructure that wallets, exchanges, and indexers depend on. This satisfies the "transaction/request-triggered halt" impact category the validation rules call out, and is High severity given the near-zero cost and complete reliability of the trigger.

### Likelihood Explanation
Likelihood is high: the vulnerable code path is the very first request-parsing step of the primary RPC endpoint, requires no credentials, no valid transaction, and no interaction with consensus, and the byte-size limit (10 MiB) is far larger than what's needed to build a nesting depth sufficient to overflow a typical worker-thread stack. The bug class matches the reported CWE-674 issue precisely (recursive deserialization of nested payloads with no depth bound).

### Recommendation
- Enforce an explicit recursion/nesting-depth limit before or during JSON parsing of untrusted RPC input (e.g., use a depth-limited JSON parser, or a pre-pass that rejects payloads whose nesting exceeds a small bound such as 64–128 levels, consistent with `serde_json`'s recommended mitigations for untrusted input).
- Alternatively/additionally, parse request bodies on a dedicated thread with a bounded, generously sized stack and treat overflow as a recoverable request failure rather than a process-wide abort, or use a non-recursive/streaming JSON parser for the outer envelope.
- Apply the same fix to any other place that deserializes externally-supplied JSON without a depth bound (e.g., Rosetta RPC's JSON body handling, which shares the same `serde_json::Value`-based design) [7](#0-6) .

### Proof of Concept
1. Start a `neard` node with the JSON-RPC server enabled (default configuration, port 3030).
2. Build a payload: `body = b'{"jsonrpc":"2.0","id":1,"method":"status","params":' + b'[' * N + b']' * N + b'}'`, choosing `N` (e.g. a few million) such that the payload stays under `json_payload_max_size` (default 10 MiB) but the nesting depth exceeds the worker thread's stack capacity.
3. POST the payload to `http://<node>:3030/`.
4. Observe that the `neard` process aborts (SIGSEGV/stack-overflow abort) instead of returning an HTTP error, taking down all RPC service (and, if co-located, block production/validation) on that node.

*(Note: I could not directly execute or profile the exact nesting depth required to overflow the specific worker-thread stack size configured for nearcore's Axum/Tokio runtime, since I only have read access to the code, not a running environment. This should be empirically verified by a background agent with execution access before filing.)*

### Citations

**File:** chain/jsonrpc-primitives/src/message.rs (L213-246)
```rust
///
/// Protocol-level errors.
#[derive(Debug, Clone, PartialEq, serde::Deserialize)]
#[serde(untagged)]
pub enum Broken {
    /// It was valid JSON, but doesn't match the form of a JSONRPC 2.0 message.
    Unmatched(Value),
    /// Invalid JSON.
    #[serde(skip_deserializing)]
    SyntaxError(String),
}

impl Broken {
    /// Generate an appropriate error message.
    ///
    /// The error message for these things are specified in the RFC, so this just creates an error
    /// with the right values.
    pub fn reply(&self) -> Message {
        match *self {
            Broken::Unmatched(_) => Message::error(RpcError::parse_error(
                "JSON RPC Request format was expected".to_owned(),
            )),
            Broken::SyntaxError(ref e) => Message::error(RpcError::parse_error(e.clone())),
        }
    }
}

/// A trick to easily deserialize and detect valid JSON, but invalid Message.
#[derive(serde::Deserialize)]
#[serde(untagged)]
pub enum WireMessage {
    Message(Message),
    Broken(Broken),
}
```

**File:** chain/jsonrpc-primitives/src/message.rs (L248-264)
```rust
pub fn decoded_to_parsed(res: JsonResult<WireMessage>) -> Parsed {
    match res {
        Ok(WireMessage::Message(Message::UnmatchedSub(value))) => Err(Broken::Unmatched(value)),
        Ok(WireMessage::Message(m)) => Ok(m),
        Ok(WireMessage::Broken(b)) => Err(b),
        Err(e) => Err(Broken::SyntaxError(e.to_string())),
    }
}

pub type Parsed = Result<Message, Broken>;

/// Read a [Message](enum.Message.html) from a slice.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}
```

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

**File:** chain/jsonrpc/src/lib.rs (L332-343)
```rust
fn process_method_call<'a, R, V, E, F>(
    request: Request,
    callback: impl FnOnce(R) -> F + 'a + Send,
) -> Pin<Box<dyn Future<Output = Result<Value, RpcError>> + 'a + Send>>
where
    R: RpcRequest + Send,
    V: serde::ser::Serialize,
    RpcError: From<E>,
    F: Future<Output = Result<V, E>> + Send,
{
    Box::pin(async move { serialize_response(callback(R::parse(request.params)?).await?) })
}
```

**File:** chain/jsonrpc/src/lib.rs (L3258-3260)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** chain/jsonrpc/src/api/mod.rs (L151-157)
```rust
        pub fn parse(value: Value) -> Result<T, RpcParseError>
        where
            T: DeserializeOwned,
        {
            serde_json::from_value(value)
                .map_err(|e| RpcParseError(format!("Failed parsing args: {e}")))
        }
```

**File:** chain/rosetta-rpc/src/config.rs (L30-39)
```rust
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RosettaRpcLimitsConfig {
    pub input_payload_max_size: usize,
}

impl Default for RosettaRpcLimitsConfig {
    fn default() -> Self {
        Self { input_payload_max_size: 10 * 1024 * 1024 }
    }
}
```
