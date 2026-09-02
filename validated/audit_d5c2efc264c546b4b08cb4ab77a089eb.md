Confirmed: for `NodeWithConfig::Sequencer`, the same `rpc_module` that gets `register_ethereum` (exposing `eth_sendRawTransaction` etc. to the public) also gets the sequencer's own methods (`create_sequencer` merges `citrea_haltCommitments`/`citrea_resumeCommitments` into it), and it's all served by one `start_rpc_server` call wrapped only by the `Auth` middleware whose `PROTECTED_METHODS` list is hardcoded to `["backup_create", "backup_validate", "backup_info"]`. Neither `citrea_haltCommitments` nor `citrea_resumeCommitments` require an API key, a signature, or any caller identity check.

### Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` RPC lets any caller stop the sequencer from publishing commitments - (File: crates/sequencer/src/rpc.rs)

### Summary
The sequencer exposes `citrea_haltCommitments` and `citrea_resumeCommitments` on its public JSON-RPC endpoint with no authentication, while functionally-similar administrative endpoints (`backup_create`, `backup_validate`, `backup_info`) are explicitly protected by the `Auth` middleware's API-key check.

### Finding Description
`SequencerRpcServerImpl::halt_commitments` and `::resume_commitments` [1](#0-0)  unconditionally forward `SequencerRpcMessage::HaltCommitments` / `::ResumeCommitments` to the sequencer runner with no caller check. The runner relays this straight to the `CommitmentService`, which toggles `is_producing_commitments` and stops/resumes L2-commitment submission to the DA layer [2](#0-1)  and [3](#0-2) .

The node's RPC transport-layer `Auth` middleware only checks a hardcoded allowlist of "protected" methods that require an API key: `PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"]` [4](#0-3) ; every other method, including `citrea_haltCommitments`/`citrea_resumeCommitments`, is passed straight through with `if !PROTECTED_METHODS.contains(&method) { return Box::pin(service.call(req)); }` [5](#0-4) .

In `bin/citrea/src/main.rs`, for a `NodeWithConfig::Sequencer`, the sequencer RPC methods (which include the halt/resume methods) are merged into the very same `rpc_module` that also serves the public Ethereum JSON-RPC methods (`eth_sendRawTransaction`, etc.), and the whole module is started with a single `start_rpc_server` call [6](#0-5) . There is no separate bind address, no role check, and no API-key gate specific to these two methods — they are reachable by exactly the same unauthenticated audience as regular `eth_sendRawTransaction` submitters.

This is directly analogous to the reported Migrations bug class: a state-mutating, privileged-sounding function (`setCompleted`/here, halt/resume of commitment production) that the codebase's own security model (the `Auth`/API-key gate used elsewhere for admin actions) intends to restrict, but the access-control check was never wired up for this particular method, so it is callable by anyone.

### Impact Explanation
Any unauthenticated party who can reach the sequencer's RPC port can call `citrea_haltCommitments` to permanently stop the sequencer from posting `SequencerCommitment`s to Bitcoin, with no way for other participants to detect or override it short of a node operator restarting/patching the sequencer. Since sequencer commitments are the rollup's mechanism for anchoring L2 state to L1 (and hence for eventual proof generation and withdrawal processing), silently halting them breaks the invariant that "honest sequencer progress is reflected in L1 commitments," effectively freezing rollup finality and any withdrawal flow that depends on commitments/proofs advancing.

### Likelihood Explanation
High. No credentials, roles, or special network position are required — this is the standard unauthenticated JSON-RPC surface. `citrea_haltCommitments` and `citrea_resumeCommitments` take no arguments, so a single RPC call is sufficient.

### Recommendation
Add `citrea_haltCommitments` and `citrea_resumeCommitments` to `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs` (or otherwise gate them behind the existing API-key/`Auth` mechanism), matching the protection already given to `backup_create`/`backup_validate`/`backup_info`.

### Proof of Concept
1. Start a sequencer node with default config (no special auth needed for non-backup methods).
2. From any unauthenticated client, call:
   ```
   curl -X POST -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"citrea_haltCommitments","params":[]}' \
     http://<sequencer-rpc-host>:<port>
   ```
   as demonstrated by the existing test harness call `sequencer.client.http_client().halt_commitments().await?` with no credentials [7](#0-6) .
3. Observe that no new commitments are submitted to the DA layer even as new L2 blocks are produced [8](#0-7) , confirming any anonymous caller can halt commitment production indefinitely.

### Citations

**File:** crates/sequencer/src/rpc.rs (L407-425)
```rust
    /// Halt sequencer commitments
    async fn halt_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_haltCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::HaltCommitments)
            .map_err(|e| internal_rpc_error(format!("Could not send halt commitments signal: {e}")))
    }

    /// Resume sequencer commitments
    async fn resume_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_resumeCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::ResumeCommitments)
            .map_err(|e| {
                internal_rpc_error(format!("Could not send resume commitments signal: {e}"))
            })
    }
```

**File:** crates/sequencer/src/runner.rs (L1271-1286)
```rust
                        Some(SequencerRpcMessage::HaltCommitments) => {
                            // Forward halt signal to commitment service
                            if let Err(e) = halt_commitment_tx.send(true) {
                                error!("Failed to send halt signal to commitment service: {}", e);
                            } else {
                                info!("Sequencer: Halted commitments via RPC");
                            }
                        },
                        Some(SequencerRpcMessage::ResumeCommitments) => {
                            // Forward resume signal to commitment service
                            if let Err(e) = halt_commitment_tx.send(false) {
                                error!("Failed to send resume signal to commitment service: {}", e);
                            } else {
                                info!("Sequencer: Resumed commitments via RPC");
                            }
                        },
```

**File:** crates/sequencer/src/commitment/service.rs (L118-144)
```rust
                // Handle halt signals from the runner
                halt_signal = self.halt_rx.recv() => {
                    match halt_signal {
                        Some(should_halt) => {
                            let should_run = !should_halt;
                            if self.is_producing_commitments != should_run {
                                self.is_producing_commitments = should_run;
                                if should_halt {
                                    warn!("CommitmentService: Commitments halted via RPC");
                                } else {
                                    info!("CommitmentService: Commitments resumed via RPC");
                                }
                            }
                        }
                        None => {
                            // Channel closed, should shutdown
                            warn!("CommitmentService: Halt signal channel closed");
                            return;
                        }
                    }
                },
                _ = check_new_block_tick.tick() => {
                    // Skip commitment processing if not running
                    if !self.is_producing_commitments {
                        debug!("CommitmentService: Skipping commitment processing (halted)");
                        continue;
                    }
```

**File:** crates/common/src/rpc/auth.rs (L11-17)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];

#[derive(Debug, Clone)]
pub struct Auth<S> {
    service: S,
    api_key: Option<String>,
}
```

**File:** crates/common/src/rpc/auth.rs (L36-38)
```rust
        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }
```

**File:** bin/citrea/src/main.rs (L270-297)
```rust
    match node_type {
        NodeWithConfig::Sequencer(sequencer_config) => {
            let (mut sequencer, rpc_module) = rollup_blueprint
                .create_sequencer(
                    genesis_config,
                    rollup_config.clone(),
                    sequencer_config,
                    da_service,
                    ledger_db,
                    storage_manager,
                    l2_block_tx,
                    rpc_module,
                    backup_manager,
                    task_executor.clone(),
                )
                .expect("Could not start sequencer");

            start_rpc_server(rollup_config.rpc.clone(), &task_executor, rpc_module, None);

            task_executor.spawn_critical_with_graceful_shutdown_signal(
                "sequencer",
                |shutdown_signal| async move {
                    if let Err(e) = sequencer.run(shutdown_signal).await {
                        error!("Error: {}", e);
                    }
                },
            );
        }
```

**File:** bin/citrea/tests/bitcoin/sequencer_commitments.rs (L358-359)
```rust
        // publish blocks, no commitments should be sent
        sequencer.client.http_client().halt_commitments().await?;
```

**File:** bin/citrea/tests/mock/sequencer_behaviour.rs (L743-764)
```rust
    // Halt commitments via RPC
    seq_test_client.sequencer_halt_commitments().await.unwrap();

    // Wait a bit for the halt signal to be processed
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Create more blocks - should not result in commitments while halted
    for _ in 0..3 {
        seq_test_client.send_publish_batch_request().await;
    }

    // Wait for potential commitments (should not happen)
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Verify no new commitments were published while halted
    // Since the sequencer is halted, no new DA blocks should be published
    // We should still be at DA block 3
    let current_da_height = da_service.get_height().await;
    assert_eq!(
        current_da_height, 3,
        "No L1 block should have been produced while halted"
    );
```
