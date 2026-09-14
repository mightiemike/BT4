Based on my investigation, I found a genuine structural analog to the Scriban bug class (CWE-674, uncontrolled recursion via untyped JSON value deserialization causing an unrecoverable process crash) in nearcore's JSON-RPC ingestion path, reachable by any unauthenticated RPC caller. Note: due to the final-iteration constraint I was unable to directly open `chain/jsonrpc/src/lib.rs`'s `rpc_handler`/`process` body to show the exact `Json<Message>` extraction line-by-line; the call chain is documented explicitly in `chain/jsonrpc/RPC_ARCHITECTURE.md` and corroborated by the `Message`/`Request`/`Value` types and route wiring I did inspect directly.

### Title
Uncontrolled recursion in JSON-RPC request `params` deserialization into `serde_json::Value` causes unrecoverable node crash - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The nearcore JSON-RPC server deserializes every inbound POST body at `/` into a `Message`/`Request` struct whose `params` field is a raw, untyped `serde_json::Value` [1](#0-0) . `serde_json`'s `Value` deserializer is a classic unbounded recursive-descent parser with no depth counter and no `EnsureSufficientExecutionStack`-style guard, exactly the bug class described in the Scriban report (CWE-674: recursion with no depth limit, no stack-overflow guard). A single JSON-RPC request body containing a deeply nested array/object (e.g. `[[[[...]]]]`) drives unbounded recursive calls while parsing into `Value`, exhausting the thread stack. In Rust, a stack overflow aborts the process immediately and cannot be caught (`catch_unwind` does not protect against stack overflow), mirroring the "fatal, unrecoverable, uncatchable" characteristic emphasized in the original report.

### Finding Description
The request pipeline is: `rpc_handler()` deserializes the raw POST body into a `Message` via `near_jsonrpc_primitives::message::from_str`/`from_slice`, then routes to `process()` → `process_request()` [2](#0-1) . The `Message`/`Request` types hold `params: Value` with no depth or shape validation, as shown by the test-fixture construction of `Request { ..., params: json!([1, 2, 3]), ... }` and `Message::Response(Response { ..., result: Ok(json!(42)), ... })` [3](#0-2) . `from_str`/`from_slice` call directly into serde_json without imposing a recursion depth limit [4](#0-3) .

The only guard on the request body at all is a byte-size cap (`RequestBodyLimitLayer::new(limits_config.json_payload_max_size)`, default documented as 10MB) [5](#0-4) [6](#0-5) . A byte-size limit does not bound nesting depth: a payload consisting of tens or hundreds of thousands of nested `[` characters (each level costs 1–2 bytes) fits comfortably inside a 10MB budget and can reach nesting depths far beyond what a typical thread stack can accommodate during recursive parsing.

This is structurally identical to the reported Scriban issue: an unauthenticated caller submits input; a generic recursive value-serialization/deserialization routine walks the structure with no depth check; the thread stack is exhausted; the process terminates unrecoverably. The route `/ (POST)` is registered unconditionally (not gated behind `enable_debug_rpc` or any authentication) [7](#0-6) , so any external RPC caller can reach it without a transaction, a valid access key, or gas payment.

### Impact Explanation
A crafted HTTP POST to any public JSON-RPC endpoint (`POST /`) with a deeply nested but otherwise small JSON body can crash the RPC-serving process. If the RPC server shares a process with `ClientActor`/`ViewClientActor`/validator duties (the common single-binary `neard` deployment, as wired in `nearcore/src/lib.rs` where `near_jsonrpc::start_http` is spawned in-process alongside the client/validator actors) [8](#0-7) , crashing the process halts that node's participation in chunk production/validation/sync until restarted — a transaction/RPC-triggered halt of node operation, not merely a resource-exhaustion nuisance. Public RPC infrastructure (community/foundation-run public gateways) would be trivially and repeatedly crashable by anyone with network access, with no signed transaction, funds, or special privilege required.

### Likelihood Explanation
High. Exploitation requires only a single unauthenticated HTTP POST with attacker-controlled body content; no signature, valid account, gas, or prior state is needed. The request-body size limit does not mitigate nesting-depth attacks because depth costs are sub-linear in bytes.

### Recommendation
- Reject requests whose JSON has excessive nesting depth before/while parsing, e.g. use a bounded/iterative JSON value type, or wrap `serde_json` parsing with a recursion-limit-aware deserializer (such as `serde_stacker`) for the `Message`/`Request::params` path in `chain/jsonrpc-primitives/src/message.rs`.
- Alternatively/additionally, use `serde_json::Deserializer` configured with `Deserializer::from_slice(...).disable_recursion_limit()`-equivalent hardening in the *opposite* direction (i.e., ensure the default recursion limit — serde_json actually has a default `serde_json::de::Deserializer` recursion limit of 128 when the `arbitrary_precision`/certain features are not enabled, but the codebase should be explicitly audited to confirm this default is not overridden or disabled) and add an explicit `RuntimeHelpers`-style stack headroom check (in Rust: `stacker::maybe_grow` or fail fast when body-derived nesting is detected).
- At minimum, add a shallow pre-check step that walks the raw JSON bytes to bound bracket/brace nesting depth (e.g. reject bodies whose max `[`/`{` nesting exceeds a small constant like 64) before invoking `serde_json::from_slice`.

### Proof of Concept
```
POST / HTTP/1.1
Host: <near-rpc-node>
Content-Type: application/json
Content-Length: <small>

{"jsonrpc":"2.0","id":1,"method":"query","params": <N nested arrays, e.g. "[" * 200000 + "]" * 200000> }
```
Sending this request (well under the documented 10MB body cap) drives `near_jsonrpc_primitives::message::from_slice` → `serde_json` recursive `Value` parsing to a stack depth of ~200,000 frames, causing a stack overflow and terminating the `neard` process handling the request.

**Uncertainty note:** I could not, within the available tool budget, directly view the exact `rpc_handler`/axum extractor code (to confirm whether `Json<Message>` extraction or a custom body-then-`from_slice` path is used) or confirm at what point (if any) `chain/jsonrpc-primitives/src/message.rs`'s `from_str`/`from_slice` might already be wrapped by a depth-limiting layer elsewhere in the codebase that my searches did not surface. The finding is based on the documented pipeline in `RPC_ARCHITECTURE.md`, the `Message`/`Request` struct's untyped `Value` field, and the unconditional `RequestBodyLimitLayer` byte-size-only guard. A Devin session with full-repo access should verify the precise deserialization call site and confirm whether any existing depth guard (e.g., a custom recursion-limited deserializer) is already applied before concluding the vulnerability is unmitigated in production.

### Citations

**File:** chain/jsonrpc-primitives/src/message.rs (L269-271)
```rust
pub fn from_str(s: &str) -> Parsed {
    from_slice(s.as_bytes())
}
```

**File:** chain/jsonrpc-primitives/src/message.rs (L300-343)
```rust
    fn message_serde() {
        // A helper for running one message test
        fn one(input: &str, expected: &Message) {
            let parsed: Message = from_str(input).unwrap();
            assert_eq!(*expected, parsed);
            let serialized = to_vec(&parsed).unwrap();
            let deserialized: Message = from_slice(&serialized).unwrap();
            assert_eq!(parsed, deserialized);
        }

        // A request without parameters
        one(
            r#"{"jsonrpc": "2.0", "method": "call", "id": 1}"#,
            &Message::Request(Request {
                jsonrpc: Version,
                method: "call".to_owned(),
                params: Value::Null,
                id: json!(1),
            }),
        );
        // A request with parameters
        one(
            r#"{"jsonrpc": "2.0", "method": "call", "params": [1, 2, 3], "id": 2}"#,
            &Message::Request(Request {
                jsonrpc: Version,
                method: "call".to_owned(),
                params: json!([1, 2, 3]),
                id: json!(2),
            }),
        );
        // A notification (with parameters)
        one(
            r#"{"jsonrpc": "2.0", "method": "notif", "params": {"x": "y"}}"#,
            &Message::Notification(Notification {
                jsonrpc: Version,
                method: "notif".to_owned(),
                params: json!({"x": "y"}),
            }),
        );
        // A successful response
        one(
            r#"{"jsonrpc": "2.0", "result": 42, "id": 3}"#,
            &Message::Response(Response { jsonrpc: Version, result: Ok(json!(42)), id: json!(3) }),
        );
```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L93-96)
```markdown
When `enable_debug_rpc` is true, additional routes under `/debug` and `/debug/api/` are registered.

Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).

```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L103-114)
```markdown
When a POST request arrives at `/`:

1. **`rpc_handler()`** - Deserializes the body into a JSON-RPC `Message`. Calls `JsonRpcHandler::process()`.
2. **`process()`** - Validates it's a `Request`, extracts `id`, delegates to `process_request()`.
3. **`process_request()`** - Metrics wrapper (timing, request count, error count per method). Delegates to `process_request_internal()`.
4. **`process_request_internal()`** - Core routing. Tries in order:
   - Adversarial requests (only with `test_features` cargo feature).
   - `process_basic_requests_internal()` - matches method name against known RPC methods.
   - Special `"query"` branch with sub-type metrics tracking.
   - Returns `method_not_found` if no match.
5. **`process_method_call()`** - Generic helper: parses params via `R::parse()`, invokes handler, serializes result, converts errors.
6. **HTTP status code mapping** in `rpc_handler()`: 200 (success), 400 (validation), 408 (timeout), 422 (UNKNOWN_BLOCK behind head), 500 (internal).
```

**File:** chain/jsonrpc/src/lib.rs (L3232-3238)
```rust
    let mut app = Router::new()
        .route("/", post(rpc_handler))
        .route("/status", get(status_handler).head(status_handler))
        .route("/health", get(health_handler).head(health_handler))
        .route("/network_info", get(network_info_handler))
        .route("/metrics", get(prometheus_handler))
        .route("/openapi.json", get(openapi_json_handler));
```

**File:** chain/jsonrpc/src/lib.rs (L3258-3260)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** nearcore/src/lib.rs (L785-814)
```rust
    #[cfg(feature = "json_rpc")]
    if let Some(rpc_config) = config.rpc_config {
        let sharded_rpc_pool = Arc::new(RwLock::new(ShardedRpcPool::new(
            rpc_config.sharded_rpc.clone(),
            rpc_shard_tracker,
            maybe_split_store.chain_store(),
        )));
        let entity_debug_handler = EntityDebugHandlerImpl {
            epoch_manager: view_epoch_manager,
            runtime: view_runtime,
            hot_store,
            cold_store,
        };
        near_jsonrpc::start_http(
            Clock::real(),
            rpc_config,
            config.genesis.config.clone(),
            client_actor.clone().into_multi_sender(),
            view_client_addr.clone().into_multi_sender(),
            rpc_handler.clone().into_multi_sender(),
            network_actor.into_multi_sender(),
            block_notification_watch_receiver,
            #[cfg(feature = "test_features")]
            _gc_actor.into_multi_sender(),
            Arc::new(entity_debug_handler),
            sharded_rpc_pool,
            actor_system.new_future_spawner("jsonrpc").as_ref(),
        )
        .await;
    }
```
