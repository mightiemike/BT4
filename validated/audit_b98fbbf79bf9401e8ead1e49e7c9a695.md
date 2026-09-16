## Analysis: Analog Found

The reported bug pattern is a "pause" safety mechanism that exists to block an action, but a code path bypasses that check entirely, letting the guarded behavior continue and cause harm. The `sequencer--025` codebase has a directly analogous flaw around the `accept_new_txs` pause flag.

### Title
Paused Gateway (`accept_new_txs=false`) Does Not Prevent Transaction Admission via the Mempool-P2P Path — (File: `crates/apollo_mempool_p2p/src/runner/mod.rs`)

### Summary
The sequencer exposes a dynamic "pause" switch, `accept_new_txs`, intended to stop a node from accepting and processing new transactions (e.g., during an active security incident, overload, or maintenance). This check is enforced only at the HTTP ingestion layer, but the underlying `Gateway` component's `add_tx` entry point — which is also invoked directly by the peer-to-peer transaction propagation path — performs no such check, so paused nodes keep validating and forwarding transactions received from the network.

### Finding Description
`check_new_transactions_are_allowed` gates the `accept_new_txs` dynamic-config flag only inside the HTTP handlers `add_rpc_tx` and `add_tx`: [1](#0-0) [2](#0-1) 

The actual acceptance logic lives in `GenericGateway::add_tx` / `add_tx_inner`, which performs stateless validation, stateful (Blockifier) validation, and forwards the transaction to the mempool — with no reference to `accept_new_txs` or any pause flag at all: [3](#0-2) 

Critically, the `MempoolP2pRunner`, which receives transaction batches gossiped from other peers over the network, calls `gateway_client.add_tx(GatewayInput { rpc_tx, message_metadata })` directly — completely independent of the HTTP server and its `accept_new_txs` gate: [4](#0-3) 

As a result, pausing a node via `accept_new_txs=false` (e.g. `HttpServerDynamicConfig`) only stops that node's own HTTP API from admitting new user-submitted transactions, but does nothing to stop the same node from validating and queueing transactions arriving via P2P gossip from peers, which are forwarded to the mempool for inclusion in future blocks it may propose. [5](#0-4) 

### Impact Explanation
`accept_new_txs` is the operator's only mechanism to halt a sequencer's transaction admission (e.g. in response to a discovered exploit, spam attack, or other emergency requiring the node to stop processing new transactions). Because the P2P ingestion path bypasses this control entirely, an operator who pauses a node believing it will stop accepting/propagating new transactions is wrong: the node continues validating and queuing peer-supplied transactions and can still include them when it acts as block proposer, undermining the intended incident-response control and allowing harmful/exploit transactions to continue being processed and confirmed during the very window the pause was meant to prevent.

### Likelihood Explanation
This requires no privileged access — it is triggered by ordinary transaction propagation among honest peers, or by any peer submitting a transaction via its own HTTP endpoint that then gossips to the paused node. The paused node will always process it because the check simply does not exist in the shared `add_tx`/`add_tx_inner` code path used by both HTTP and P2P.

### Recommendation
Move the `accept_new_txs` (or an equivalent pause) check into `GenericGateway::add_tx`/`add_tx_inner` in `crates/apollo_gateway/src/gateway.rs` itself, so it is enforced uniformly for both the HTTP ingestion path and the `MempoolP2pRunner` P2P ingestion path, ensuring a paused gateway truly stops all transaction admission regardless of source.

### Proof of Concept
1. Operator sets `http_server_config.dynamic_config.accept_new_txs = false` on Node A to halt new transaction admission during an incident.
2. A user submits a malicious/exploit transaction to Node B's HTTP endpoint (`accept_new_txs = true` there).
3. Node B's `MempoolP2pPropagator` gossips the transaction over the `MEMPOOL_TOPIC` gossipsub channel.
4. Node A's `MempoolP2pRunner::start` receives the broadcast and calls `gateway_client.add_tx(GatewayInput { rpc_tx, message_metadata })` directly [6](#0-5) , which reaches `GenericGateway::add_tx_inner` with no pause check, validates the transaction, and forwards it to Node A's mempool — despite the operator's pause.

### Citations

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

**File:** crates/apollo_http_server/src/http_server.rs (L228-233)
```rust
fn check_new_transactions_are_allowed(accept_new_txs: bool) -> HttpServerResult<()> {
    match accept_new_txs {
        true => Ok(()),
        false => Err(HttpServerError::DisabledError()),
    }
}
```

**File:** crates/apollo_gateway/src/gateway.rs (L191-298)
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

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();

        // Declare conversions overload the compiler component's CPU and memory. Reject declares if
        // there are too many declares compiling in parallel. The permit is held only across
        // compilation and released before stateful validation.
        let compilation_permit = if matches!(tx, RpcTransaction::Declare(_)) {
            Some(self.declare_compilation_semaphore.try_acquire().map_err(|_| {
                let error = StarknetError::too_many_concurrent_declare_compilations();
                metric_counters.record_add_tx_failure(&error);
                error
            })?)
        } else {
            None
        };

        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let proof_archive_handle = self
            .store_proof_and_spawn_archiving(proof_data, internal_tx.tx_hash, is_p2p)
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let gateway_output = create_gateway_output(&internal_tx);

        let add_tx_args = AddTransactionArgsWrapper {
            args: AddTransactionArgs::new(internal_tx, nonce),
            p2p_message_metadata,
        };

        // Await as late as possible for proof archiving before sending the transaction to the
        // mempool.
        Self::await_proof_archiving(proof_archive_handle)
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let mempool_client_result = self.mempool_client.add_tx(add_tx_args).await;
        match mempool_client_result_to_deprecated_gw_result(&tx_signature, mempool_client_result) {
            Ok(()) => {}
            Err(e) => {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        };

        metric_counters.transaction_sent_to_mempool();

        Ok(gateway_output)
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

**File:** crates/apollo_http_server_config/src/config.rs (L94-123)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct HttpServerDynamicConfig {
    pub accept_new_txs: bool,
    pub max_sierra_program_size: usize,
}

impl SerializeConfig for HttpServerDynamicConfig {
    fn dump(&self) -> BTreeMap<ParamPath, SerializedParam> {
        BTreeMap::from_iter([
            ser_param(
                "accept_new_txs",
                &self.accept_new_txs,
                "Enables accepting new txs.",
                ParamPrivacyInput::Public,
            ),
            ser_param(
                "max_sierra_program_size",
                &self.max_sierra_program_size,
                "The maximum size of a sierra program in bytes.",
                ParamPrivacyInput::Public,
            ),
        ])
    }
}

impl Default for HttpServerDynamicConfig {
    fn default() -> Self {
        Self { accept_new_txs: true, max_sierra_program_size: DEFAULT_MAX_SIERRA_PROGRAM_SIZE }
    }
}
```
