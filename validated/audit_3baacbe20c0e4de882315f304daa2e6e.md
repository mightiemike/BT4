## Analysis Result

### Title
Unbounded JSON nesting depth in gateway transaction deserialization causes stack-overflow process abort - ([File: crates/apollo_http_server/src/http_server.rs])

### Summary
Both HTTP gateway entry points that accept externally-submitted transactions — `add_rpc_tx` (RPC format) and `add_tx` (deprecated gateway format) — deserialize the raw request body with `serde_json` before any structural depth limit is applied. `serde_json`'s recursive-descent parser recurses once per `[`/`{` nesting level regardless of the target Rust type, so a small, byte-budget-compliant payload consisting mostly of deeply nested brackets can exhaust the worker thread's stack during parsing, well before Serde ever gets to validate/reject the transaction shape.

### Finding Description
`add_rpc_tx` uses axum's `Json<RpcTransaction>` extractor [1](#0-0) , and `add_tx` calls `serde_json::from_str::<DeprecatedGatewayTransactionV3>(&tx)` directly, falling back to `serde_json::from_str` into a generic `Value` on parse failure for metrics purposes [2](#0-1) [3](#0-2) .

The only guards in front of this parsing are byte-size limits: `RequestBodyLimitLayer` and `DefaultBodyLimit`, both driven by `max_request_body_size` (default 5 MiB) [4](#0-3) [5](#0-4) . These layers bound total bytes, not nesting depth. Neither `DeprecatedGatewayTransactionV3`'s `#[serde(deny_unknown_fields)]` derive nor any custom deserializer in this path imposes a recursion-depth cap, and there is no use of a stack-growing helper (e.g. `serde_stacker`) anywhere in the codebase for these HTTP entry points (confirmed via repo-wide search for `recursion_limit`/`serde_stacker`/`max_depth`, which only appear in unrelated contexts such as `execution/entry_point.rs`'s Cairo call-recursion limit and the transaction prover's `max_request_body_size`, not JSON structural depth).

Because `serde_json`'s parser is written as ordinary recursive Rust functions, deeply nested array/object tokens (e.g. `"[[[[[...]]]]]"` or nested unknown-field content that must be skipped as `IgnoredAny`/`Content` before `deny_unknown_fields` can reject it) drive the parser's call stack linearly with nesting depth. A payload of only a few hundred KB can encode hundreds of thousands of nesting levels — comfortably inside the 5 MiB body limit and far beyond a worker thread's stack (Tokio's default worker thread stack is a few MiB). In Rust, stack exhaustion is not a recoverable panic: the runtime's guard-page handler prints "thread ... has overflowed its stack" and calls `abort()`, which terminates the entire process, not just the offending task/thread — killing every other component and transaction processing co-located in that process.

### Impact Explanation
Any unauthenticated client able to reach the gateway's `/gateway/add_rpc_transaction` or `/gateway/add_transaction` HTTP endpoints can submit a single crafted request that aborts the entire sequencer process. This is a full availability loss for the node (and any other co-located components in the same process), matching the "network unable to confirm new transactions" impact bar, analogous to the reported Spring AMQP `System.exit(99)` behavior where one hostile message kills the whole JVM rather than just the handling thread.

### Likelihood Explanation
No authentication, special privileges, or chain state is required — a bare, deeply nested JSON body under the existing 5 MiB `max_request_body_size` limit is sufficient. The attack requires no valid transaction fields, signatures, or fees, since the crash happens during raw JSON tokenization/parsing, before transaction-level validation (fee, nonce, signature) is reached.

### Recommendation
Impose an explicit, low, JSON-structural nesting-depth limit before/while parsing gateway-submitted request bodies (e.g. wrap parsing with `serde_stacker::maybe_grow`, or pre-scan/reject inputs exceeding a small bracket-depth threshold) in both `add_rpc_tx` and `add_tx` in `crates/apollo_http_server/src/http_server.rs`, and in the fallback `serde_json::from_str` used by `check_supported_resource_bounds_and_increment_metrics`, independent of the existing byte-size body limits.

### Proof of Concept
1. Build a request body of the form `"[".repeat(N) + "]".repeat(N)` (or a JSON object skeleton with `N` nested arrays inside an unknown/rejected field) where `N` is large enough to exceed the parsing thread's stack (e.g. a few hundred thousand, well under the 5 MiB `max_request_body_size`).
2. POST this body to `/gateway/add_transaction` (or `/gateway/add_rpc_transaction`) on a running sequencer's HTTP server.
3. Observe the process printing "thread ... has overflowed its stack" and aborting, terminating the entire sequencer process rather than just failing the single request.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L144-162)
```rust
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

**File:** crates/apollo_http_server/src/http_server.rs (L285-294)
```rust
fn check_supported_resource_bounds_and_increment_metrics(tx: &str) {
    if let Ok(tx_json_value) = serde_json::from_str(tx) {
        if let Ok(transaction) = deserialize_transaction_json_to_starknet_api_tx(tx_json_value) {
            if let Some(ValidResourceBounds::L1Gas(_)) = transaction.resource_bounds() {
                ADDED_TRANSACTIONS_DEPRECATED_ERROR.increment(1);
            }
        }
    }
    ADDED_TRANSACTIONS_FAILURE.increment(1);
}
```

**File:** crates/apollo_http_server_config/src/config.rs (L13-15)
```rust
// The value is chosen to be much larger than the transaction size limit as enforced by the Starknet
// protocol.
const DEFAULT_MAX_REQUEST_BODY_SIZE: usize = 5 * 1024 * 1024; // 5MB
```
