### Title
Uncontrolled recursion during untrusted JSON transaction deserialization in the gateway HTTP endpoint causes process abort - ([File: crates/apollo_http_server/src/http_server.rs])

### Summary
The reported Grails CVE (CVE-2023-46131) is a class of bug where unbounded, recursive data-binding of an untrusted web request causes a JVM crash before any size/shape validation runs. The sequencer's HTTP gateway exhibits the same structural pattern: incoming transaction JSON is deserialized directly into Rust structs via `axum::Json` / `serde_json` **before** any of the gateway's stateless size limits are applied, and the byte-size limit that is enforced does not bound JSON nesting depth.

### Finding Description
The `/gateway/add_rpc_transaction` handler extracts the transaction directly as a typed value: [1](#0-0) 

and the legacy `/gateway/add_transaction` handler similarly calls `serde_json::from_str` on the raw body: [2](#0-1) 

Both paths hand the raw, attacker-controlled bytes to `serde`/`serde_json`'s recursive-descent deserializer *before* any of the gateway's stateless validation limits (`max_calldata_length`, `max_contract_bytecode_size`, `max_contract_class_object_size`, `max_signature_length`, `max_proof_size`, etc.) are ever consulted — those checks live in `StatelessTransactionValidator::validate` and only run against an already-fully-deserialized `RpcTransaction`: [3](#0-2) 

The only protection applied prior to deserialization is a **byte-count** limit on the request body: [4](#0-3) 

`RequestBodyLimitLayer`/`DefaultBodyLimit` bound total bytes, not JSON structural nesting depth. A payload consisting mostly of deeply nested array/object brackets (e.g. `[[[[[...]]]]]]`) can reach tens or hundreds of thousands of nesting levels while remaining well under any reasonable byte-size cap. Rust's/serde's recursive-descent JSON parsing (and, in particular, the generic `Content` buffering that `serde` performs internally for enum variants such as the transaction-type dispatch, e.g. the untagged `DeclareTransaction` enum seen in the gateway-facing client types) recurses once per nesting level with no application-enforced depth limit: [5](#0-4) 

Unlike an ordinary panic (which Tokio/axum can catch per-task and convert into an error response), a Rust stack overflow triggers a hardware guard-page fault that **aborts the entire process** — it cannot be caught by `catch_unwind`. This means a single crafted HTTP POST can crash the whole sequencer node process, not merely fail the one request. Note: I was not able to fully confirm from the indexed code the precise tag/untagged structure of the top-level `RpcTransaction` enum in `crates/starknet_api/src/rpc_transaction.rs` (searches only confirmed its declaration, not its serde attributes), so the exact serde dispatch mechanism used for the primary RPC entry point should be verified directly against that file before remediation.

### Impact Explanation
If confirmed, any unprivileged, unauthenticated network client that can reach the gateway's `add_transaction`/`add_rpc_transaction` HTTP endpoints can crash the sequencer process with a single request, well before stateless size checks or signature checks ever execute. Repeated exploitation prevents the affected sequencer from confirming any new transactions until manually or automatically restarted — satisfying the "network unable to confirm new transactions" impact criterion. This mirrors the original CWE-400 Grails advisory exactly: unauthenticated, single-request, pre-validation resource exhaustion via recursive data binding.

### Likelihood Explanation
Likelihood is high if the deserialization path is confirmed vulnerable: the endpoint is reachable pre-authentication by any transaction sender (this is literally the gateway's public transaction-submission surface), requires no special privileges, no valid signature, and no state knowledge — only a crafted JSON body.

### Recommendation
- Verify the serde representation of `RpcTransaction` and all nested types reachable from the gateway HTTP body (in `crates/starknet_api/src/rpc_transaction.rs`) for untagged/internally-tagged enums that trigger `serde`'s generic `Content` buffering.
- Enforce a maximum JSON nesting-depth check on the raw request body (or wrap deserialization with a depth-limited JSON deserializer) *before* handing untrusted bytes to `serde_json`/`axum::Json`, in both `add_rpc_tx` and `add_tx` in `crates/apollo_http_server/src/http_server.rs`.
- Alternatively, run the top-level HTTP deserialization step on a dedicated thread with a bounded stack and treat a stack-overflow/guard-page condition as a recoverable request failure rather than a process abort, or increase supervisory process restart isolation so one crashed connection-handling thread cannot take down the whole node.

### Proof of Concept
1. Construct an HTTP POST body to `/gateway/add_rpc_transaction` (or `/gateway/add_transaction`) consisting of `N` nested JSON arrays, e.g. `"[".repeat(200_000) + "]".repeat(200_000)`, sized to stay under `max_request_body_size`.
2. Send the request to the gateway's HTTP server.
3. `axum::Json` / `serde_json::from_str` recurse once per nesting level while parsing/buffering the value before the target type or the gateway's `validate_tx_size`/`validate_declare_tx` limits are ever reached, exhausting the worker thread's stack and aborting the process.
(This PoC's precise effectiveness depends on confirming the exact serde enum representations noted above, which the index did not allow me to fully verify.)

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

**File:** crates/apollo_http_server/src/http_server.rs (L196-210)
```rust
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
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L254-263)
```rust
/// A declare transaction that can be added to Starknet through the Starknet gateway.
/// It has a serialization format that the Starknet gateway accepts in the `add_transaction`
/// HTTP method.
#[derive(Debug, Deserialize, Serialize, Clone, Eq, PartialEq)]
#[serde(untagged)]
pub enum DeclareTransaction {
    DeclareV1(DeclareV1Transaction),
    DeclareV2(DeclareV2Transaction),
    DeclareV3(DeclareV3Transaction),
}
```
