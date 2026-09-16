### Title
`GenericGateway::add_tx_inner` never checks `accept_new_txs`, so the "pause new transactions" control only guards the HTTP entry point, not the Gateway component itself - ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
The `HttpServerDynamicConfig.accept_new_txs` flag is the sequencer's analog of a "pause" switch: an operator can flip it to stop new transactions from being admitted in an emergency. The check that enforces it, `check_new_transactions_are_allowed`, is called only inside the two Axum HTTP handlers (`add_rpc_tx`, `add_tx`) in `apollo_http_server`. The actual admission logic lives in `GenericGateway::add_tx`/`add_tx_inner` in `apollo_gateway`, which has no awareness of `accept_new_txs` at all and is reachable through a second, independent entry point: the `Gateway` component's request handler (`ComponentRequestHandler<GatewayRequest, GatewayResponse>`), used for the P2P/consensus/mempool-propagation add-transaction path (`GatewayRequest::AddTransaction`).

### Finding Description
`check_new_transactions_are_allowed` is only invoked in the HTTP handlers: [1](#0-0) [2](#0-1) 

The Gateway's actual transaction-intake function, `add_tx_inner`, performs stateless/stateful validation and forwards to the mempool with no gate on `accept_new_txs`: [3](#0-2) 

The `Gateway` is also exposed as a standalone infra component (local/remote server), whose request handler calls `self.add_tx(...)` directly, completely bypassing the HTTP layer and its pause check: [4](#0-3) 

This second path is exactly the one used for propagated transactions between sequencers, as documented in the submission-flow diagram: Sequencer B's Mempool-P2P `Runner` calls `GW_B: add_tx(GatewayInput)` directly, never going through Sequencer B's HTTP server: [5](#0-4) 

The `Gateway` type itself carries no dynamic "accept_new_txs" field; only `native_classes_whitelist` is dynamic in its config, confirming the pause flag is not part of the Gateway component's own state: [6](#0-5) 

This mirrors the reported bug class precisely: a pause mechanism exists (`accept_new_txs`/`whenNotPaused`) but is not applied on every code path that reaches the same critical operation (`create()`/`add_tx_inner`). Here, the omission is structural: the check was wired into one entry point (HTTP) but the shared underlying function (`GenericGateway::add_tx`) that both entry points call has no such guard, and the second entry point (the direct component `GatewayRequest::AddTransaction` handler used for P2P propagation) has no pause check of its own either.

### Impact Explanation
When an operator disables `accept_new_txs` on a node (e.g., during an incident, overload, or a chain-halt/upgrade procedure) intending to stop new transactions from entering that node's mempool, transactions gossiped to it via the Mempool-P2P propagation path still reach `Gateway::add_tx` → `add_tx_inner` → the mempool unimpeded, because that path never checks `accept_new_txs`. The operator's emergency stop is silently ineffective for the P2P admission surface, so the mempool keeps filling and the sequencer keeps building blocks with new transactions despite the intended pause, defeating the incident-response control the flag was designed to provide.

### Likelihood Explanation
This does not require a malicious peer or privileged actor: it triggers under completely normal operation — any regular transaction submitted anywhere on the network that legitimately propagates through Mempool-P2P to a node whose operator paused `accept_new_txs` will still be admitted by that node. The only precondition is that the operator has toggled `accept_new_txs=false` while other honest nodes continue to gossip normal transactions, which is the exact scenario the flag exists to handle.

### Recommendation
Move (or duplicate) the `accept_new_txs` check into `GenericGateway::add_tx`/`add_tx_inner` itself, so both the HTTP entry point and the `GatewayRequest::AddTransaction` component-request path enforce it uniformly. Concretely, thread the dynamic `accept_new_txs` value (already polled via `dynamic_config_poll`) into the `Gateway`/`GenericGateway` struct and return an early rejection (analogous to `HttpServerError::DisabledError`/`GatewaySpecError`) at the top of `add_tx_inner` before any validation or mempool forwarding occurs.

### Proof of Concept
1. Operator sets Node B's `HttpServerDynamicConfig.accept_new_txs = false` to pause new transaction intake on Node B (intending to stop admission entirely).
2. A client submits a normal, valid transaction to Node A's HTTP endpoint; Node A's HTTP handler passes its own `accept_new_txs` check (true) and forwards to Node A's `Gateway::add_tx` → mempool.
3. Node A's Mempool-P2P `Propagator` broadcasts the transaction; Node B's Mempool-P2P `Runner` receives it and calls Node B's `Gateway::add_tx(GatewayInput)` directly via the `GatewayRequest::AddTransaction` component path, as shown in `crates/apollo_gateway/src/communication.rs`.
4. `add_tx_inner` on Node B performs no `accept_new_txs` check and successfully validates and forwards the transaction to Node B's mempool, proving Node B's pause was ineffective against P2P-propagated transactions.

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

**File:** crates/apollo_gateway/src/gateway.rs (L214-298)
```rust
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

**File:** crates/apollo_gateway/src/communication.rs (L19-36)
```rust
#[async_trait]
impl ComponentRequestHandler<GatewayRequest, GatewayResponse> for Gateway {
    async fn handle_request(&mut self, request: GatewayRequest) -> GatewayResponse {
        match request {
            GatewayRequest::AddTransaction(gateway_input) => {
                let p2p_message_metadata = gateway_input.message_metadata.clone();
                GatewayResponse::AddTransaction(
                    self.add_tx(gateway_input.rpc_tx, gateway_input.message_metadata)
                        .await
                        .map_err(|source| GatewayError::DeprecatedGatewayError {
                            source,
                            p2p_message_metadata,
                        }),
                )
            }
        }
    }
}
```

**File:** docs/diagrams/02-tx-submission-flow.md (L17-53)
```markdown
    box Sequencer B
        participant Runner as Mempool P2P<br/>Runner
        participant GW_B as Gateway
        participant MP_B as Mempool
    end

    User->>HTTP: POST /add_transaction
    HTTP->>GW: add_tx(GatewayInput)

    Note over GW: Stateless validation<br/>(format, signature)

    alt Declare Transaction
        GW->>CM: add_class(SierraContractClass)
        CM->>Compiler: compile(RawClass)
        Compiler-->>CM: RawExecutableClass
        CM-->>GW: Sierra & CASM Hashes
    end

    rect rgb(240, 248, 255)
        Note over GW,SS: Stateful validation (via Blockifier)
        GW->>SS: get_nonce_at(block_number, contract_address)
        SS-->>GW: Nonce
        GW->>SS: read state (balance, storage, etc.)
        SS-->>GW: state data
    end

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
```
