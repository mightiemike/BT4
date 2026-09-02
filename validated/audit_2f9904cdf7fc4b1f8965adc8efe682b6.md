### Title
Unauthenticated batch-prover JSON-RPC methods bypass `Auth` and allow arbitrary mutation of prover state — (File: `crates/common/src/rpc/auth.rs`, `crates/batch-prover/src/rpc.rs`)

### Summary
The RPC authentication middleware protects only a hard-coded allow-list of three method names, while all other JSON-RPC methods — including several state-mutating administrative methods on the batch-prover (`setCommitments`, `submitFakeProof`, `pauseProving`, `prove`, `retryProvingJob`) — pass through unauthenticated, even when an `api_key` is configured.

### Finding Description
`Auth::call` only checks for an API key when the requested method name is in `PROTECTED_METHODS`: [1](#0-0) 

`PROTECTED_METHODS` contains exactly `["backup_create", "backup_validate", "backup_info"]`. Every other registered JSON-RPC method — regardless of how privileged or destructive — is dispatched to the underlying service with no key check at all, as long as it is reachable on the bound RPC port: [2](#0-1) 

The batch prover registers exactly this class of privileged, state-mutating methods on the same RPC server/middleware stack (`start_rpc_server` in `crates/common/src/rpc/server.rs` wires the same `Auth` layer for every module merged into it): [3](#0-2) 

The exposed, unauthenticated batch-prover methods include:
- `batchProver_setCommitments` — directly overwrites `SequencerCommitmentByIndex`/pending-commitment/L1-index tables in the prover's ledger DB with attacker-supplied `merkle_root`, `index`, `l2_end_block_number` values, no validation against what the sequencer actually posted on L1: [4](#0-3) 

- `batchProver_submitFakeProof` — triggers native re-execution and submission of a proof (using a `FakeReceipt`) to the DA for an attacker-chosen commitment index range: [5](#0-4) 

- `batchProver_pauseProving`, `batchProver_prove`, `batchProver_retryProvingJob` — control the prover's proving lifecycle: [6](#0-5) [7](#0-6) 

None of these method names appear in `PROTECTED_METHODS`, so they are callable by anyone who can reach the RPC endpoint, independent of the configured `api_key`. This is structurally the same bug class as the referenced report: a small allow-list ("auth"-checked path) exists, but numerous state-mutating operations bypass it entirely because they were never added to the checked set — analogous to Ladle.sol/PoolRouter.sol's `batch()` exposing internal privileged operations (`_redeem`, `_exitEther`, `_moduleCall`) without the same access-control wrapper applied to the outer, "authorized" entry points.

### Impact Explanation
An unauthenticated caller reaching the batch-prover's RPC port can corrupt the prover node's local view of sequencer commitments (`setCommitments`), pause/resume its proving pipeline, retry arbitrary jobs, and force submission of fake-receipt proofs to the DA. This satisfies the High-severity bar defined by the scan rules ("an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`"), since the `Auth` middleware exists specifically to gate privileged operations but fails to cover this entire class of destructive methods.

### Likelihood Explanation
Likelihood is high wherever the batch-prover's RPC endpoint is network-reachable (e.g., bound beyond localhost, or reachable through internal infrastructure): no credentials, signature, or role check of any kind is required to invoke these methods — only knowledge of the JSON-RPC method name, which is public in the source and OpenRPC/trait definitions.

### Recommendation
Extend `PROTECTED_METHODS` (or replace the allow-list model with a default-deny model) to cover every state-mutating/administrative RPC method registered by the batch-prover (and audit other modules, e.g. sequencer's `citrea_haltCommitments`/`citrea_resumeCommitments`, for the same gap), so that the `Auth` middleware enforces the configured `api_key` on all privileged operations rather than only a hard-coded backup-related subset.

### Proof of Concept
1. Start a batch-prover node with `rpc_config.api_key` configured (intending to restrict privileged access) and the RPC port reachable by a third party.
2. From an unauthenticated client (no API key parameter), send:
```json
{"jsonrpc":"2.0","id":1,"method":"batchProver_setCommitments","params":[[{"merkleRoot":"0x...","index":"0x5","l2EndBlockNumber":"0x64","l1Height":"0x1"}]]}
```
This succeeds and overwrites the stored commitment at index 5, as shown by the direct DB writes in `set_commitments` [8](#0-7) .
3. Follow with an unauthenticated `batchProver_pauseProving` or `batchProver_submitFakeProof` call — both succeed with no key required, since neither method name is present in `PROTECTED_METHODS` [9](#0-8) .

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-38)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];

#[derive(Debug, Clone)]
pub struct Auth<S> {
    service: S,
    api_key: Option<String>,
}

impl<S> Auth<S> {
    pub fn new(service: S, api_key: Option<String>) -> Self {
        Self { service, api_key }
    }
}

impl<'a, S> RpcServiceT<'a> for Auth<S>
where
    S: RpcServiceT<'a> + Send + Sync + Clone + 'a,
{
    type Future = BoxFuture<'a, MethodResponse>;

    fn call(&self, req: Request<'a>) -> Self::Future {
        let method = req.method_name();
        let service = self.service.clone();
        let api_key = self.api_key.clone().map(Value::from);

        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }
```

**File:** crates/common/src/rpc/server.rs (L42-45)
```rust
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);
```

**File:** crates/batch-prover/src/rpc.rs (L176-209)
```rust
    #[method(name = "setCommitments")]
    async fn set_commitments(&self, commitments: Vec<SequencerCommitmentRpcParam>)
        -> RpcResult<()>;

    /// Manually signal proving. This rpc triggers a proving signal with the difference that sampling will be ignored.
    ///
    /// # Arguments
    /// * `mode` - The partition mode to use for proving.
    #[method(name = "prove")]
    async fn prove(&self, mode: PartitionMode) -> RpcResult<Vec<Uuid>>;

    /// Simulate proving by collecting output from the execution in native, and submit the fake proof to DA.
    ///
    /// # Arguments
    /// * `index_start` - The starting index of the commitment range to submit a fake proof for.
    /// * `index_end` - The ending index of the commitment range to submit a fake proof for.
    /// Important caveats regarding the arguments:
    /// - `index_start` must be greater than 1, as the first commitment index requires special handling.
    /// - `index_end` must be greater than or equal to `index_start`.
    /// - The range is inclusive, meaning both `index_start` and `index_end` are included in the proof.
    /// - The previous index to `index_start` must exist in the ledger database if `index_start` is greater than 1.
    ///
    /// # Returns
    /// A `BatchProofResponse` containing the L1 transaction ID, proof, and proof output.
    #[method(name = "submitFakeProof")]
    async fn submit_fake_proof(
        &self,
        index_start: u32,
        index_end: u32,
    ) -> RpcResult<BatchProofResponse>;

    /// Stop further proving jobs to be spawned. Existing jobs will continue.
    #[method(name = "pauseProving")]
    async fn pause_proving(&self) -> RpcResult<()>;
```

**File:** crates/batch-prover/src/rpc.rs (L299-307)
```rust
    /// Retry a proving job by its ID. This will re-queue the job for proving, and return a new job ID.
    ///
    /// # Arguments
    /// * `job_id` - The unique identifier of the proving job to retry.
    ///
    /// # Returns
    /// A new `Uuid` representing the retried proving job.
    #[method(name = "retryProvingJob")]
    async fn retry_proving_job(&self, job_id: Uuid) -> RpcResult<Uuid>;
```

**File:** crates/batch-prover/src/rpc.rs (L345-381)
```rust
    async fn set_commitments(
        &self,
        commitments: Vec<SequencerCommitmentRpcParam>,
    ) -> RpcResult<()> {
        for commitment in commitments {
            let l1_height = commitment.l1_height.to::<u64>();
            let commitment = SequencerCommitment {
                merkle_root: commitment.merkle_root,
                index: commitment.index.to::<u32>(),
                l2_end_block_number: commitment.l2_end_block_number.to::<u64>(),
            };

            info!(
                "Overriding sequencer commitment, index={} merkle_root={} l2_end_height={} l1_height={}",
                commitment.index,
                hex::encode(commitment.merkle_root),
                commitment.l2_end_block_number,
                l1_height,
            );

            self.context
                .ledger_db
                .put_commitment_by_index(&commitment)
                .map_err(internal_rpc_error)?;
            // This might cause some duplicate commitment indices appear in l1 -> index table which is ok
            self.context
                .ledger_db
                .put_commitment_index_by_l1(SlotNumber(l1_height), commitment.index)
                .map_err(internal_rpc_error)?;
            self.context
                .ledger_db
                .put_prover_pending_commitment(commitment.index)
                .map_err(internal_rpc_error)?;
        }

        Ok(())
    }
```

**File:** crates/batch-prover/src/rpc.rs (L405-425)
```rust
    async fn submit_fake_proof(
        &self,
        index_start: u32,
        index_end: u32,
    ) -> RpcResult<BatchProofResponse> {
        info!(
            "Submitting fake proof for commitment index range [{},{}]",
            index_start, index_end
        );

        let ledger_db = &self.context.ledger_db;

        if index_start > index_end {
            return Err(internal_rpc_error("Invalid index range"));
        }
        // don't allow first commitment index to be called through this rpc as it requires extra handling
        if index_start <= 1 {
            return Err(internal_rpc_error(
                "submitFakeProof rpc supports only index_start > 1",
            ));
        }
```
