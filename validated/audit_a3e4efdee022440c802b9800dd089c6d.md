## Title
Unauthenticated `citrea_haltCommitments` / `batchProver_pauseProving` RPC calls allow any network caller to freeze sequencer commitments and batch-proving — (File: `crates/common/src/rpc/auth.rs`)

### Summary
All sequencer and batch-prover RPC methods are merged into the same single `jsonrpsee::RpcModule<()>` and served from one JSON-RPC endpoint through a shared `Auth` middleware layer [1](#0-0) . That middleware only requires an API key for three whitelisted method names — the backup endpoints — and lets every other method through unauthenticated.

### Finding Description
The `Auth` RPC middleware defines a hard-coded allowlist of protected methods: [2](#0-1) 
and its `call()` implementation immediately forwards any request whose method name is **not** in `PROTECTED_METHODS` without checking the API key at all: [3](#0-2) 

`PROTECTED_METHODS` contains only `backup_create`, `backup_validate`, `backup_info`. It does not include the sequencer's commitment-control methods (`citrea_haltCommitments`, `citrea_resumeCommitments`) or the batch prover's proving-control methods (`batchProver_pauseProving`, `batchProver_setCommitments`, `batchProver_prove`, `batchProver_submitFakeProof`). These are registered on the very same RPC server/port as the public Ethereum JSON-RPC API:
- `citrea_haltCommitments` / `citrea_resumeCommitments` forward a halt/resume signal straight to the commitment service without any caller check: [4](#0-3) 
- The commitment service loop honors this halt flag and stops producing/submitting sequencer commitments to DA while halted: [5](#0-4) 
- `batchProver_pauseProving` sends a `ProverRequest::Pause` with no caller check: [6](#0-5) [7](#0-6) 
- Once paused, `try_proving()` unconditionally short-circuits and returns no jobs, meaning the batch prover stops proving submitted commitments: [8](#0-7) 

Since these RPC methods are reachable by any unauthenticated network client on the public JSON-RPC port (the same design flaw class as the report's "pausing a critical protocol operation freezes state that must keep advancing"), an unprivileged attacker can silently halt sequencer commitment production and/or pause batch proving indefinitely by simply making one RPC call each — no credentials, no role, no key required.

### Impact Explanation
This crosses the "High" bar defined for this analog class: an unauthenticated JSON-RPC call that mutates node state and bypasses `Auth`. Halting commitments stops sequencer commitments from being posted to Bitcoin DA (breaking the invariant that L2 state keeps advancing on L1), and pausing the batch prover stops proof generation/submission for already-posted commitments, stalling the proven L2 height and therefore the light-client/bridge state that ultimately relies on proofs to unlock withdrawals. Both are single unauthenticated RPC calls (`citrea_haltCommitments`, `batchProver_pauseProving`) that any caller reaching the RPC endpoint can issue.

### Likelihood Explanation
High. The methods require no special role, wallet, or key; they are ordinary JSON-RPC calls to the default node RPC endpoint that is typically exposed for the public Ethereum-compatible API (`eth_*`, `citrea_*`). Because `Auth::call()` defaults to "allow" for any method not explicitly listed, this is a design/default-config gap rather than a misuse of documented configuration — the code performs no authorization check for the sequencer/batch-prover admin RPC surface at all.

### Recommendation
Add `citrea_haltCommitments`, `citrea_resumeCommitments`, `batchProver_pauseProving`, `batchProver_setCommitments`, `batchProver_prove`, `batchProver_submitFakeProof`, and `batchProver_createCircuitInput` (and any other node/prover control method) to `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs`, or switch to an explicit allowlist-of-public-methods model instead of a denylist-of-protected-methods model so that newly added admin RPCs are protected by default.

### Proof of Concept
1. Start a sequencer node with the default RPC config (no `api_key` needed for non-backup calls, or even with an `api_key` set — it's irrelevant since the method isn't checked).
2. From any unauthenticated client, call:
```json
{"jsonrpc":"2.0","id":1,"method":"citrea_haltCommitments","params":[]}
```
3. Observe (as shown in the existing test `test_sequencer_halt_resume_commitments` in `bin/citrea/tests/mock/sequencer_behaviour.rs`) that no new commitments are produced/sent to DA even though L2 blocks continue to be produced.
4. Similarly call `batchProver_pauseProving` on the batch prover's RPC endpoint; subsequent `batchProver_prove` invocations return zero job ids as confirmed by `bin/citrea/tests/mock/proving.rs:370-383`, demonstrating proving is fully halted by an unauthenticated caller.

### Citations

**File:** crates/common/src/rpc/server.rs (L42-45)
```rust
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);
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

**File:** crates/common/src/rpc/auth.rs (L31-38)
```rust
    fn call(&self, req: Request<'a>) -> Self::Future {
        let method = req.method_name();
        let service = self.service.clone();
        let api_key = self.api_key.clone().map(Value::from);

        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }
```

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

**File:** crates/batch-prover/src/rpc.rs (L207-209)
```rust
    /// Stop further proving jobs to be spawned. Existing jobs will continue.
    #[method(name = "pauseProving")]
    async fn pause_proving(&self) -> RpcResult<()>;
```

**File:** crates/batch-prover/src/rpc.rs (L535-541)
```rust
    async fn pause_proving(&self) -> RpcResult<()> {
        self.context
            .request_tx
            .send(ProverRequest::Pause)
            .await
            .map_err(|_| internal_rpc_error("Proving request channel is closed"))
    }
```

**File:** crates/batch-prover/src/prover.rs (L279-287)
```rust
    async fn try_proving(
        &mut self,
        mode: PartitionMode,
        with_sampling: bool,
    ) -> anyhow::Result<Vec<Uuid>> {
        if self.proving_paused {
            debug!("Proving is paused");
            return Ok(Vec::new());
        }
```
