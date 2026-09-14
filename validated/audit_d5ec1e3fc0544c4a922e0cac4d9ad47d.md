Confirmed: `create_jsonrpc_app` applies only a byte-size limit (`RequestBodyLimitLayer::new(limits_config.json_payload_max_size)`, default 10 MiB) to the JSON-RPC POST body, with no JSON-nesting-depth limit before `axum::Json<Message>` invokes `serde_json` to recursively deserialize the `Message`/`Value` tree. [1](#0-0) [2](#0-1)  The `Request`/`Notification`/`Message` types carry `params: serde_json::Value`, whose `Deserialize` is unboundedly recursive over nested arrays/objects. [3](#0-2) 

### Title
Unbounded recursive JSON deserialization on the JSON-RPC `/` endpoint allows stack-overflow DoS via deeply nested `params` - (File: `chain/jsonrpc/src/lib.rs`, `chain/jsonrpc-primitives/src/message.rs`)

### Summary
The GNU Unrtf CVE is a stack overflow caused by recursively parsing an attacker-controlled input (a crafted `filename`) without any recursion-depth bound. Nearcore's JSON-RPC server has the same bug class: the `/` endpoint accepts an arbitrary JSON-RPC `Message`, whose `params` field is a generic `serde_json::Value`. `serde_json`'s `Value` deserializer recurses once per nesting level of the input (`[[[[...]]]]` or `{"a":{"a":{"a":...}}}`), and nearcore enforces only a total byte-size cap (10 MiB by default), not a nesting-depth cap.

### Finding Description
- `create_jsonrpc_app()` wires the POST `/` route to `rpc_handler`, which extracts the body via `axum::extract::Json<Message>` before any application logic runs. [2](#0-1) 
- The only middleware applied is CORS and `RequestBodyLimitLayer::new(limits_config.json_payload_max_size)` (default `10485760` bytes per the shipped example config). [1](#0-0) [4](#0-3) 
- `Message`/`Request`/`Notification` hold `params: serde_json::Value`, so an unauthenticated JSON-RPC caller fully controls a JSON value whose deserialization is delegated to `serde_json`'s recursive-descent `Value` deserializer, which has no built-in depth limit. [3](#0-2) 
- A payload consisting almost entirely of nested `[` characters (e.g., `{"jsonrpc":"2.0","method":"status","id":1,"params":` + N `[` + N `]` + `}`) stays well under the 10 MiB size cap while making the parser recurse N times, one native stack frame per bracket. With N in the hundreds of thousands to low millions (easily fitting in a few MB), this exhausts the request-handling task's stack before the byte-limit is ever reached.
- Nowhere in the RPC request pipeline (`process()`, `process_request()`, `process_request_internal()`, `Params::parse`/`unwrap_or_parse`, which itself calls `serde_json::from_value`) is a depth check performed before or during this recursive parse. [5](#0-4) 
- This mirrors mitigations nearcore already applies elsewhere for recursion hazards reachable from untrusted input — e.g., `NonDelegateAction`'s hand-rolled Borsh deserializer explicitly rejects nested delegate actions to avoid unbounded recursion [6](#0-5) , and the WASM runtime instruments every function call with explicit stack-height accounting to trap before a native overflow occurs [7](#0-6)  — but no equivalent guard exists for JSON-RPC request bodies.

### Impact Explanation
A stack overflow in the async task handling the HTTP request typically aborts the process (Rust has no safe recovery from a real stack overflow — it is not a catchable `panic`), which crashes the whole `neard` process, not just the request. Because JSON-RPC is exposed on every full/RPC node (default port 3030) and is reachable by any external, unauthenticated caller who can reach that port, this is a transaction-free, single-request Denial of Service against node/validator availability. If the same code path or a shared `serde_json` dependency were exercised on a validator's RPC surface, this could interrupt validator operations, but even limited to view-only/gateway nodes this degrades network availability and is consistent with the CVE's "DoS via crafted input causing stack overflow" bug class.

### Likelihood Explanation
High for reachability (any RPC caller, no authentication, no fee payment, no on-chain transaction required — just an HTTP POST). The rules restrict scope to unprivileged transaction signers, contract deployers, stakers, or RPC callers reaching validation/runtime/RPC surfaces; the JSON-RPC endpoint is explicitly in scope. Exploitability depends on the actual recursion limit before native stack exhaustion under the specific `serde_json` version and thread stack size configured for the axum worker; this was not independently verified in the sandboxed index (no local build/run available), so likelihood should be validated by an actual PoC run in a Devin session before being treated as fully confirmed.

### Recommendation
- Wrap `serde_json` deserialization of client-controlled RPC bodies with a bounded-recursion facility (e.g. `serde_stacker::maybe_grow`, or upgrading to a `serde_json` configuration that supports the `RecursionLimit`/`Deserializer::from_slice` depth guard) before extracting into `Message`/`Value`.
- Alternatively/additionally, pre-scan or reject request bodies whose bracket/brace nesting exceeds a small, protocol-reasonable bound (e.g. 64–128) prior to full deserialization.
- Apply the same guard to the Rosetta RPC server, which shares the same `axum::Json` + size-limit-only pattern. [8](#0-7) 

### Proof of Concept
```
POST / HTTP/1.1
Host: <node>:3030
Content-Type: application/json
Content-Length: <small>

{"jsonrpc":"2.0","method":"status","id":1,"params":[[[[[[[[[[ ... repeated ~1,000,000 times ... ]]]]]]]]]]}
```
Send a body built as the literal prefix `{"jsonrpc":"2.0","method":"status","id":1,"params":` followed by N repetitions of `[` and then N repetitions of `]`, choosing N large enough (well under the 10 MiB `json_payload_max_size`) to exceed the axum/tokio worker task's stack. The `axum::extract::Json<Message>` extractor in `rpc_handler` will recursively parse the array into `serde_json::Value` and overflow the stack before any RPC method logic (`process_request_internal`) executes. [2](#0-1)

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

**File:** chain/jsonrpc/src/lib.rs (L3258-3261)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
}
```

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

**File:** nearcore/res/example-config-gc.json (L21-23)
```json
        "limits_config": {
            "json_payload_max_size": 10485760
        }
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

**File:** core/primitives/src/action/delegate.rs (L360-371)
```rust
/// This is Action which mustn't contain DelegateAction.
///
/// This struct is needed to avoid the recursion when Action/DelegateAction is deserialized.
///
/// Important: Don't make the inner Action public, this must only be constructed
/// through the correct interface that ensures the inner Action is actually not
/// a delegate action. That would break an assumption of this type, which we use
/// in several places. For example, borsh de-/serialization relies on it. If the
/// invariant is broken, we may end up with a `Transaction` or `Receipt` that we
/// can serialize but deserializing it back causes a parsing error.
#[derive(Serialize, BorshSerialize, Deserialize, PartialEq, Eq, Clone, Debug, ProtocolSchema)]
pub struct NonDelegateAction(Action);
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L257-271)
```rust
pub fn finite_wasm_stack(
    ctx: &mut Ctx,
    _memory: &mut [u8],
    operand_size: u64,
    frame_size: u64,
) -> Result<()> {
    ctx.remaining_stack =
        match ctx.remaining_stack.checked_sub(operand_size.saturating_add(frame_size)) {
            Some(s) => s,
            None => return Err(VMLogicError::HostError(HostError::MemoryAccessViolation)),
        };
    let gas = ((frame_size + 7) / 8) * u64::from(ctx.config.regular_op_cost);
    consume_gas(&mut ctx.result_state.gas_counter, gas)?;
    Ok(())
}
```

**File:** chain/rosetta-rpc/src/lib.rs (L1108-1112)
```rust
        .merge(SwaggerUi::new("/swagger-ui").url("/api/openapi.json", RosettaOpenApi::openapi()))
        .with_state(app_state)
        .layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits.input_payload_max_size))
        .layer(TraceLayer::new_for_http());
```
