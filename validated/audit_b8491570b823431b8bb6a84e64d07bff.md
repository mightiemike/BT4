### Title
`accept_new_txs` pause flag is enforced only at the HTTP layer and is bypassed by transactions arriving via P2P gossip - ([File: crates/apollo_http_server/src/http_server.rs])

### Summary
The sequencer exposes an operator "pause new transactions" control (`accept_new_txs`, part of `HttpServerDynamicConfig`) that is meant to stop the node from admitting new transactions. This check is enforced only in the HTTP server's `add_rpc_tx`/`add_tx` handlers, not in the `Gateway::add_tx` entry point itself. Transactions that arrive over the mempool P2P gossip network are forwarded directly to `GatewayClient::add_tx` by `MempoolP2pRunner`, completely skipping the HTTP layer and therefore the `accept_new_txs` gate — the same "alternate entry point bypasses the pause guard" pattern described in the referenced GoGoPool `restakeGGP`/`whenNotPaused` finding.

### Finding Description
The pause-like admission gate is implemented as: [1](#0-0) [2](#0-1) 

Both `add_rpc_tx` and `add_tx` (the two HTTP routes registered in `HttpServer::app`) call `check_new_transactions_are_allowed(accept_new_txs)` before forwarding the request to `app_state.gateway_client.add_tx(...)`: [3](#0-2) 

However, `GenericGateway::add_tx` / `add_tx_inner` — the actual function that performs stateless/stateful validation and forwards the transaction to the mempool — has no knowledge of `accept_new_txs` at all: [4](#0-3) 

The mempool P2P runner, which receives transactions gossiped from peer sequencers, calls `gateway_client.add_tx(GatewayInput { rpc_tx, message_metadata })` directly — it never goes through the HTTP server or the `accept_new_txs` check: [5](#0-4) 

This matches the documented submission flow, where the P2P path re-enters the gateway independently of the HTTP path: [6](#0-5) 

### Impact Explanation
If an operator sets `accept_new_txs = false` (e.g., during an incident, upgrade, or to halt transaction intake for safety), the node still accepts and processes transactions relayed by peers over the mempool P2P network, adds them to the local mempool, and — since mempool admission feeds directly into block building — they can still be included in built/validated blocks. This breaks the intended operational safety control: an operator believing new-transaction intake is halted can still have the node admit, propagate further, and sequence transactions sourced from other peers, undermining the purpose of the pause (e.g., stopping abuse, mitigating a live incident, or performing a controlled halt before an upgrade).

### Likelihood Explanation
Likelihood is high in any deployment using P2P gossip (i.e., any production topology with more than one sequencer/mempool peer), since it requires no special privilege — a single unprivileged transaction sender only needs to submit a transaction to any other peer in the network (or to the paused node's own peers) to have it gossip into the "paused" node via `MempoolP2pRunner`, requiring no cooperation from a malicious operator or node.

### Recommendation
Enforce the `accept_new_txs` gate (or an equivalent check) inside `GenericGateway::add_tx`/`add_tx_inner`, so it applies uniformly regardless of whether the transaction originates from the HTTP server or from the P2P `MempoolP2pRunner`. Alternatively, thread the dynamic config through to the gateway client used by `MempoolP2pRunner` and perform the same `check_new_transactions_are_allowed` check before calling `gateway_client.add_tx` in the P2P path.

### Proof of Concept
1. Operator sets `http_server_config.dynamic_config.accept_new_txs = false` on sequencer node A to halt new transaction intake (verifiable in `HttpServerDynamicConfig`, gated by `check_new_transactions_are_allowed` in `crates/apollo_http_server/src/http_server.rs` lines 167–198).
2. A user submits an unprivileged transaction to a different, non-paused peer sequencer node B (or directly injects a gossip message reaching node A's mempool-p2p network layer).
3. Node A's `MempoolP2pRunner::start` receives the broadcast message via `broadcasted_topic_server` and calls `gateway_client.add_tx(GatewayInput { rpc_tx, message_metadata })` (`crates/apollo_mempool_p2p/src/runner/mod.rs` lines 104–128) — this call path never touches `check_new_transactions_are_allowed`.
4. `GenericGateway::add_tx_inner` (`crates/apollo_gateway/src/gateway.rs` lines 214–298) runs full validation and forwards the transaction to node A's mempool via `mempool_client.add_tx`, despite `accept_new_txs = false` on node A.

Note: I could not locate the definition/body of `check_new_transactions_are_allowed` or `HttpServerDynamicConfig::accept_new_txs` in the indexed content (only the call sites were found), so the exact semantics of the flag (e.g., whether it's meant to reject only client-submitted, non-P2P transactions by design) could not be fully confirmed from the index. If `accept_new_txs` is intentionally scoped to only the direct client-facing HTTP endpoints (and P2P forwarding is an accepted, documented exception), this would not be a vulnerability — a Devin session with full repo access should verify the config's doc-comment and any related tests (e.g., `crates/apollo_http_server_config/src/config.rs`) before treating this as confirmed.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L126-145)
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

**File:** crates/apollo_http_server/src/http_server.rs (L183-198)
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
```

**File:** crates/apollo_gateway/src/gateway.rs (L191-233)
```rust
    #[sequencer_latency_histogram(GATEWAY_ADD_TX_LATENCY, true)]
    pub async fn add_tx(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        debug!("Processing tx: {:?}", &tx);
        let tx_signature = tx.signature().clone();
        let is_p2p = p2p_message_metadata.is_some();

        let start_time = std::time::Instant::now();
        let ret = self.add_tx_inner(tx, p2p_message_metadata).await;
        let elapsed = start_time.elapsed().as_secs_f64();

        debug!(
            "Processed tx with signature: {:?}. duration: {elapsed} sec, ret: {ret:?}, is_p2p: \
             {is_p2p}",
            &tx_signature,
        );

        ret
    }

    async fn add_tx_inner(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();
        if let RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref inv)) = tx {
            if !inv.proof_facts.is_empty() {
                metric_counters.count_private_transaction_received();
            }
        }
        let is_p2p = p2p_message_metadata.is_some();

        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }
```

**File:** crates/apollo_mempool_p2p/src/runner/mod.rs (L104-128)
```rust
                Some((message_result, broadcasted_message_metadata)) = self.broadcasted_topic_server.next() => {
                    match message_result {
                        Ok(message) => {
                            // TODO(alonl): consider calculating the tx_hash and printing it instead of the entire tx.
                            debug!("Received transaction batch from network, forwarding to gateway. Batch: {:?}", message.0);
                            for rpc_tx in message.0 {
                                let permit = match gateway_semaphore.clone().try_acquire_owned() {
                                    Ok(permit) => permit,
                                    Err(_) => {
                                        warn!(
                                            "Rejecting transaction due to backpressure. \
                                             Transaction: {rpc_tx:?}"
                                        );
                                        continue;
                                    }
                                };
                                let gateway_client = self.gateway_client.clone();
                                let message_metadata = Some(broadcasted_message_metadata.clone());
                                gateway_futures.push(async move {
                                    let _permit = permit;
                                    gateway_client.add_tx(
                                        GatewayInput { rpc_tx, message_metadata }
                                    ).await
                                });
                            }
```

**File:** docs/diagrams/02-tx-submission-flow.md (L43-52)
```markdown
    GW->>MP: add_tx(AddTransactionArgsWrapper)
    MP-->>GW: Ok
    GW-->>HTTP: tx_hash
    HTTP-->>User: tx_hash

    MP->>Prop: add_transaction(InternalRpcTransaction)
    Prop->>Runner: broadcast (P2P)
    Runner->>GW_B: add_tx(GatewayInput)
    Note over GW_B: Same validation flow
    GW_B->>MP_B: add_tx(AddTransactionArgsWrapper)
```
