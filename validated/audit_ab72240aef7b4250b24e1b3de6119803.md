### Title
Denial of Service via Unbounded JSON Nesting Depth in Gateway Transaction Deserialization - (File: crates/apollo_http_server/src/http_server.rs)

### Summary
The HTTP gateway endpoints that accept transactions from unprivileged senders (`add_rpc_tx` and `add_tx`) deserialize the entire request body directly with `serde_json` (via `axum::Json<RpcTransaction>` and `serde_json::from_str::<DeprecatedGatewayTransactionV3>`) with no JSON nesting-depth guard, analogous to the tink-cc `JsonKeysetReader` bug class (CVE-2024-4420) where a recursive-descent JSON parser crashes/stack-overflows on deeply nested input before any semantic validation occurs.

### Finding Description
`add_rpc_tx` uses the `Json<RpcTransaction>` extractor, which parses the raw HTTP body with `serde_json` before any of `starknet_api`'s field-level validation runs [1](#0-0) . Similarly, `add_tx` calls `serde_json::from_str(&tx)` directly on the untrusted body string to produce a `DeprecatedGatewayTransactionV3` [2](#0-1) . `DeprecatedGatewayTransactionV3` and its nested transaction structs are marked `#[serde(deny_unknown_fields)]` [3](#0-2) , which means an unknown/extra JSON key's value must still be parsed and recursively skipped (via `serde::de::IgnoredAny`) before it can be rejected — a well-known recursion path in `serde_json`-based deserializers.

The only guard on the request body is a byte-size cap: `max_request_body_size` (default 5 MiB, `DEFAULT_MAX_REQUEST_BODY_SIZE`) enforced via `RequestBodyLimitLayer`/`DefaultBodyLimit` [4](#0-3)  and configured in `apollo_http_server_config` [5](#0-4) . This cap bounds the number of bytes, not the JSON nesting depth: a 5 MiB payload consisting almost entirely of repeated `[` characters (e.g., in an unknown field or a field whose declared type does not match, forcing `serde_json`'s recursive-descent parser or the `IgnoredAny` skip path to recurse once per nesting level) can encode millions of nesting levels well within the size limit. `serde_json` has no built-in recursion-depth limit for this recursive-descent path, so a sufficiently deep structure can exhaust the call stack.

No search in the repo turned up any custom recursion-depth limiting deserializer, `RUST_MIN_STACK` tuning for the gateway/HTTP-server binaries, or a "streaming with max depth" JSON reader analogous to what would be needed to defend against this — that mitigation only exists incidentally in `native_blockifier`'s `.cargo/config.toml` (`RUST_MIN_STACK = 4 MiB`), which is unrelated to the HTTP gateway service and to recursion-depth limits at the JSON-parsing layer itself [6](#0-5) .

### Impact Explanation
A stack overflow in Rust is not a catchable panic — it aborts the process (SIGSEGV/SIGABRT). Because the HTTP gateway (`apollo_http_server`) is the ingress for all unprivileged transaction submissions, crashing this process denies all new transaction intake for that sequencer node, i.e., "a network unable to confirm new transactions" from that node until it is restarted. If deployed with multiple gateway replicas behind a load balancer, this is a repeatable per-request crash primitive that an attacker can send at any/all instances.

### Likelihood Explanation
Reachable by any unauthenticated/unprivileged client able to send a single HTTP POST to `/gateway/add_transaction` or `/gateway/add_rpc_transaction`; no signature, fee payment, or account state is required to reach the deserialization step, since parsing happens before nonce/fee/signature validation. The payload is trivial to construct (repeated `[` characters) and fits well inside the default 5 MiB body limit.

### Recommendation
- Impose an explicit maximum JSON nesting depth check before/while deserializing untrusted transaction bodies (e.g., pre-scan bracket/brace depth, or use a `serde_json::Deserializer` configured with a depth-limiting wrapper) in `add_rpc_tx` / `add_tx` in `crates/apollo_http_server/src/http_server.rs`.
- Alternatively/additionally, run body-to-struct deserialization for these entrypoints in a dedicated thread with a bounded stack and treat stack-overflow/abort as an isolated task failure rather than a whole-process crash, or use a streaming validator that rejects oversized/over-nested documents prior to full parse.
- Add a regression test that posts a deeply nested (but size-limit-compliant) JSON body to both endpoints and asserts a graceful 4xx response rather than a process crash.

### Proof of Concept
1. Construct a payload such as `"a".repeat(0) ` — more concretely: `let nested = "[".repeat(2_000_000) + &"]".repeat(2_000_000);` and wrap it as the value of an unknown JSON key alongside a syntactically otherwise-valid `DeprecatedGatewayTransactionV3`/`RpcTransaction` body (or simply as the whole body if the target field itself accepts arbitrary JSON), keeping total size under 5 MiB.
2. `POST` this body to `/gateway/add_transaction` (or `/gateway/add_rpc_transaction`) with `Content-Type: application/json`.
3. Observe the `apollo_http_server` process crash with a stack overflow / SIGSEGV instead of returning a `400`-class error, taking down transaction ingestion for that node.

Note: I could not execute this PoC (no runtime/filesystem access in this environment); the finding is based on static analysis of the deserialization call sites, `deny_unknown_fields` usage, and the absence of any depth-limiting guard in the reachable code paths cited above. This should be verified by actually running the gateway binary and sending the crafted payload.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L126-163)
```rust
    pub fn app(&self) -> Router {
        Router::new()
            // Json Rpc endpoint
            .route(
                "/gateway/add_rpc_transaction",
                self.post_method_router(add_rpc_tx),
            )
            // Rest api endpoint
            .route(
                "/gateway/add_transaction",
                self.post_method_router(add_tx),
            )
            // TODO(shahak): Remove this once we fix the centralized simulator to not use is_alive
            // and is_ready.
            .route(
                "/gateway/is_alive",
                get(|| futures::future::ready("Gateway is alive".to_owned()))
            )
            .route("/gateway/is_ready", get(is_ready))
            .layer(Extension(self.app_state.clone()))
            // Hard streaming limit on decompressed bytes — wraps the body in
            // http_body_util::Limited which errors during poll_frame() once the
            // limit is exceeded, preventing zip bombs from expanding in memory.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
            .layer(RequestDecompressionLayer::new())
            // Cap compressed wire bytes to bound network I/O.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
    }

    fn post_method_router<H, T, S>(&self, handler: H) -> MethodRouter<S>
    where
        H: Handler<T, S> + Send + Sync + 'static,
        T: Send + 'static,
        S: Clone + Send + Sync + 'static,
    {
        post(handler).layer(DefaultBodyLimit::max(self.config.static_config.max_request_body_size))
    }
}
```

**File:** crates/apollo_http_server/src/http_server.rs (L167-181)
```rust
#[instrument(skip(app_state, tx))]
async fn add_rpc_tx(
    Extension(app_state): Extension<AppState>,
    headers: HeaderMap,
    Json(tx): Json<RpcTransaction>,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("ADD_TX_START: Http server received a new transaction.");

    let HttpServerDynamicConfig { accept_new_txs, .. } = app_state.get_dynamic_config();
    check_new_transactions_are_allowed(accept_new_txs)?;

    ADDED_TRANSACTIONS_TOTAL.increment(1);
    set_unix_now_seconds(&LAST_RECEIVED_TRANSACTION_TIMESTAMP_SECONDS);
    add_tx_inner(app_state, headers, tx).await
}
```

**File:** crates/apollo_http_server/src/http_server.rs (L183-217)
```rust
#[instrument(skip(app_state, tx))]
#[sequencer_latency_histogram(HTTP_SERVER_ADD_TX_LATENCY, true)]
async fn add_tx(
    Extension(app_state): Extension<AppState>,
    headers: HeaderMap,
    tx: String,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("ADD_TX_START: Http server received a new transaction.");

    let HttpServerDynamicConfig { accept_new_txs, max_sierra_program_size } =
        app_state.get_dynamic_config();
    check_new_transactions_are_allowed(accept_new_txs)?;

    ADDED_TRANSACTIONS_TOTAL.increment(1);
    set_unix_now_seconds(&LAST_RECEIVED_TRANSACTION_TIMESTAMP_SECONDS);
    let tx: DeprecatedGatewayTransactionV3 = match serde_json::from_str(&tx) {
        Ok(value) => value,
        Err(e) => {
            validate_supported_tx_version_str(&tx).inspect_err(|e| {
                debug!("Error while validating transaction version: {}", e);
                increment_failure_metrics(e);
            })?;

            debug!("Error while parsing transaction: {}", e);
            check_supported_resource_bounds_and_increment_metrics(&tx);
            return Err(e.into());
        }
    };

    let rpc_tx = tx.convert_to_rpc_tx(max_sierra_program_size).inspect_err(|e| {
        debug!("Error while converting deprecated gateway transaction into RPC transaction: {}", e);
    })?;

    add_tx_inner(app_state, headers, rpc_tx).await
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L41-51)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
#[serde(tag = "type")]
#[serde(deny_unknown_fields)]
pub enum DeprecatedGatewayTransactionV3 {
    #[serde(rename = "DECLARE")]
    Declare(DeprecatedGatewayDeclareTransaction),
    #[serde(rename = "DEPLOY_ACCOUNT")]
    DeployAccount(DeprecatedGatewayDeployAccountTransaction),
    #[serde(rename = "INVOKE_FUNCTION")]
    Invoke(DeprecatedGatewayInvokeTransaction),
}
```

**File:** crates/apollo_http_server_config/src/config.rs (L11-16)
```rust
const HTTP_SERVER_PORT: u16 = 8080;
pub const DEFAULT_MAX_SIERRA_PROGRAM_SIZE: usize = 4 * 1024 * 1024; // 4MB
// The value is chosen to be much larger than the transaction size limit as enforced by the Starknet
// protocol.
const DEFAULT_MAX_REQUEST_BODY_SIZE: usize = 5 * 1024 * 1024; // 5MB
const DEFAULT_DYNAMIC_CONFIG_POLL_INTERVAL_MS: u64 = 1_000; // 1 second.
```

**File:** crates/native_blockifier/.cargo/config.toml (L1-7)
```text
[env]
# Enforce native_blockifier linking with pypy3.9.
PYO3_PYTHON = "/usr/local/bin/pypy3.9"
# Increase Rust stack size.
# This should be large enough for `MAX_ENTRY_POINT_RECURSION_DEPTH` recursive entry point calls.
RUST_MIN_STACK = "4194304" #  4 MiB

```
