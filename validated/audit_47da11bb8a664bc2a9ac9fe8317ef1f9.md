### Title
Denial of Service via panic-on-input crash from a submitted transaction, escalated to full process exit - (File: `crates/apollo_node/src/main.rs`)

### Summary
The external advisory describes a CORS header handler in `@commercial/hapi` that throws an unhandled exception on malformed input, and — absent an exception handler — crashes the whole application. In this repository the equivalent bug class (an input-triggered `panic!`/`.unwrap()`/`.expect()` reachable from untrusted, attacker-controlled data causing the process to die) is structurally amplified: `apollo_node` installs a global panic hook that force-exits the entire process on **any** panic anywhere in the async runtime, and additionally wraps monitored tasks with `spawn_with_exit_on_panic`, which also calls `std::process::exit(1)` if a task panics.

### Finding Description
In `crates/apollo_node/src/main.rs`, `set_exit_process_on_panic` installs a global panic hook: [1](#0-0) 
This means that **any unhandled panic in any task or thread of the sequencer process** — including one triggered by user-supplied transaction data during gateway request handling — brings down the entire node, not just the single request. This is the direct analog of the hapi vulnerability: an externally-reachable code path that can throw/panic on invalid input, with no isolation between the offending handler and the rest of the running service.

Compounding this, `apollo_infra_utils::tasks::spawn_with_exit_on_panic` explicitly re-implements the same "panic anywhere ⇒ exit the whole process" behavior for monitored component tasks: [2](#0-1) 

The gateway/HTTP transaction ingestion path is exactly the kind of externally reachable surface the advisory rules require (a single submitted transaction from an unprivileged sender): `add_tx`/`add_rpc_tx` in `crates/apollo_http_server/src/http_server.rs` parse attacker-controlled JSON and hex-encoded fields, feeding them through `serde_json::from_str`, `DeprecatedGatewayTransactionV3::convert_to_rpc_tx`, and `StatelessTransactionValidator::validate` before any stateful validation occurs: [3](#0-2) 

The codebase's own style guide explicitly documents this bug class as a known risk ("Never panic on data reachable from requests" — code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input) and a regression test exists specifically to prevent one instance of it (malformed `resource_bounds` must return `Err`, not panic): [4](#0-3) [5](#0-4) 

This shows the team is already aware individual panics-on-input have occurred and been patched piecemeal (e.g., the `.expect("should be map of resource bounds")` in `deserialize_transaction_json_to_starknet_api_tx`, guarded today by prior null/type checks): [6](#0-5) 

However, the mitigation strategy is per-callsite (finding and fixing individual `.unwrap()`s as they're discovered), while the systemic amplifier — a global "any panic terminates the whole node" hook plus a "any monitored task panic terminates the whole node" wrapper — remains in place. Any *future or currently-undiscovered* panic anywhere in the transaction ingestion, conversion, or validation pipeline (parsing, hex decoding, resource-bounds handling, calldata/signature length arithmetic, Sierra/CASM related code, etc.) is one attacker-controlled request away from taking down the entire sequencer, not merely failing one request — exactly the "no unhandled exception handler ⇒ shut down services" scenario the hapi advisory warns about.

### Impact Explanation
If any single reachable panic exists (or is later introduced) in code that processes transaction-derived data before validation completes, a single malicious transaction submission can crash the entire sequencer process via the `main.rs` panic hook or the `tasks.rs` exit-on-panic wrapper, taking down block production/transaction admission for the whole node — a network-level denial of service reachable by any unprivileged transaction sender, satisfying the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
Likelihood is Medium: the specific instance found in `serde_utils.rs` appears already guarded by preceding null checks and is covered by a regression test, so it is not by itself an actively-exploitable panic today. However, the systemic design (global panic-to-exit hook + task-level exit-on-panic) means the security depends entirely on *zero* panics existing anywhere in the large, evolving transaction-processing surface (gateway, deprecated gateway transaction conversion, resource-bounds/DA-mode/serde logic) — a single future regression (as the project's own style-guide entry acknowledges is a recurring risk) turns an isolated bug into a full outage rather than a contained request failure.

### Recommendation
- Isolate request/transaction processing from the node's fatal panic hook: catch panics per-request (e.g., `std::panic::catch_unwind` at the HTTP handler boundary or task boundary) and convert to an error response instead of triggering `std::process::exit`.
- Reconsider `spawn_with_exit_on_panic`'s use in components that directly process untrusted transaction input; reserve fatal-exit-on-panic for truly unrecoverable invariant violations (e.g., storage corruption), not user-input-adjacent code.
- Continue and expand the existing `.claude/rules/code-style.md` audit for `.unwrap()`/`.expect()`/panicking indexing anywhere in the gateway/HTTP/serde transaction pipeline, and add fuzz testing over transaction JSON to catch new panics before they reach production.

### Proof of Concept
Conceptual PoC (exact panic-triggering payload not confirmed in this investigation):
1. Submit a transaction to `/gateway/add_transaction` or `/gateway/add_rpc_transaction` with a field value that would trigger a panic in the deserialize/convert/validate chain (`crates/apollo_http_server/src/http_server.rs` `add_tx`/`add_rpc_tx`).
2. Because `apollo_node::main::set_exit_process_on_panic` installs a global panic hook that calls `std::process::exit(1)` on any panic, the panic — normally isolated to the async task handling that one request — terminates the entire sequencer process.
3. All in-flight and subsequent transaction processing on that node halts until the process is restarted.

Note: this analysis did not locate a currently-unguarded, concretely-exploitable panic-on-input in the sampled code (the one located instance in `serde_utils.rs` is guarded); the finding centers on the systemic panic-to-process-exit design being the true analog of the hapi "no unhandled exception handler" DoS pattern, which converts any individual overlooked panic into a full-node outage. A background Devin session with full repo/test access would be needed to fuzz the transaction ingestion pipeline for an actual triggering payload.

### Citations

**File:** crates/apollo_node/src/main.rs (L22-28)
```rust
fn set_exit_process_on_panic() {
    let default_panic = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        default_panic(info);
        std::process::exit(1);
    }));
}
```

**File:** crates/apollo_infra_utils/src/tasks.rs (L30-63)
```rust
pub fn spawn_with_exit_on_panic<F, T>(future: F) -> JoinHandle<T>
where
    F: Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    inner_spawn_with_exit_on_panic(future, exit_process)
}

// Use an inner function to enable injecting the exit function for testing.
pub(crate) fn inner_spawn_with_exit_on_panic<F, E, T>(future: F, on_exit_f: E) -> JoinHandle<T>
where
    F: Future<Output = T> + Send + 'static,
    E: FnOnce() + Send + 'static,
    T: Send + 'static,
{
    // Spawn the first task to execute the future
    let monitored_task = tokio::spawn(future);

    // Spawn the second task to await the first task and assert its completion
    tokio::spawn(async move {
        match monitored_task.await {
            Ok(res) => res,
            Err(err) => {
                error!("Monitored task failed: {:?}", err);
                on_exit_f();
                unreachable!()
            }
        }
    })
}

pub(crate) fn exit_process() {
    std::process::exit(1);
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

**File:** .claude/rules/code-style.md (L67-70)
```markdown
### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```

**File:** crates/starknet_api/src/serde_utils_test.rs (L191-211)
```rust
/// V3 transaction JSON may arrive from untrusted sources; a missing or malformed
/// `resource_bounds` field must surface as a deserialization error, not a panic.
#[test]
fn deserialize_transaction_json_does_not_panic_on_malformed_resource_bounds() {
    // Missing resource_bounds field.
    let raw_transaction = serde_json::json!({"type": "INVOKE", "version": "0x3"});
    assert!(deserialize_transaction_json_to_starknet_api_tx(raw_transaction).is_err());

    // resource_bounds is not an object.
    let raw_transaction =
        serde_json::json!({"type": "INVOKE", "version": "0x3", "resource_bounds": 5});
    assert!(deserialize_transaction_json_to_starknet_api_tx(raw_transaction).is_err());

    // l1_gas without l2_gas.
    let raw_transaction = serde_json::json!({
        "type": "DECLARE",
        "version": "0x3",
        "resource_bounds": {"l1_gas": {"max_amount": "0x0", "max_price_per_unit": "0x0"}}
    });
    assert!(deserialize_transaction_json_to_starknet_api_tx(raw_transaction).is_err());
}
```

**File:** crates/starknet_api/src/serde_utils.rs (L181-199)
```rust
pub fn deserialize_transaction_json_to_starknet_api_tx(
    mut raw_transaction: Value,
) -> serde_json::Result<Transaction> {
    let tx_type: String = serde_json::from_value(raw_transaction["type"].clone())?;
    let tx_version: String = serde_json::from_value(raw_transaction["version"].clone())?;

    // rpc_v8 fix (remove redundantly added L1DataGas)
    let raw_resourcebounds = &raw_transaction["resource_bounds"];
    if !raw_resourcebounds.is_null()
        && !raw_resourcebounds["l1_data_gas"].is_null()
        && raw_resourcebounds["l1_data_gas"]["max_amount"] == "0x0"
        && !raw_resourcebounds["l2_gas"].is_null()
        && raw_resourcebounds["l2_gas"]["max_amount"] == "0x0"
    {
        raw_transaction["resource_bounds"]
            .as_object_mut()
            .expect("should be map of resource bounds")
            .remove("l1_data_gas");
    }
```
