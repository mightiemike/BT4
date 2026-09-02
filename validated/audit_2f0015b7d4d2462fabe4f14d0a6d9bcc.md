### Title
Unauthenticated `set_commitments` batch-prover RPC forges sequencer commitments without sender/signature verification - (File: `crates/batch-prover/src/rpc.rs`)

### Summary
The batch-prover's `set_commitments` JSON-RPC method writes arbitrary, caller-supplied `SequencerCommitment` data directly into the same ledger-DB tables (`SequencerCommitmentByIndex`, `CommitmentIndicesByL1`, `ProverPendingCommitments`) that are otherwise only populated after verifying the commitment actually came from the sequencer's DA blob. This method is not in the RPC `Auth` middleware's protected-method list, so it is reachable by any unauthenticated network caller, letting an attacker inject a forged commitment/index/L1-height mapping that the batch prover subsequently treats as ground truth for proving.

### Finding Description
`set_commitments` iterates caller-supplied commitments and, for each one, calls `put_commitment_by_index`, `put_commitment_index_by_l1`, and `put_prover_pending_commitment` using the attacker-chosen `index`, `merkle_root`, `l2_end_block_number`, and `l1_height` fields, with no validation against the DA layer: [1](#0-0) 

This mirrors the report's bug class: a batch setter writes into the same key space (`SequencerCommitmentByIndex` indexed by `index`, `CommitmentIndicesByL1` keyed by `l1_height`) that the trusted/legitimate path populates, but bypasses the invariants that path enforces. Contrast with the legitimate path in the light-client and full-node DA scanners, which only accept a `SequencerCommitment` after checking `blob.sender().as_ref() == sequencer_da_public_key`: [2](#0-1) 

and the fullnode handler, which requires the commitment's predecessor to exist and its Merkle root to match the actual synced L2 blocks before storing it: [3](#0-2) [4](#0-3) 

`set_commitments` has none of these checks — it does not verify the commitment was signed/sent by the sequencer, does not check sequentiality of `index`, and does not verify the Merkle root against actual L2 block hashes.

Critically, this RPC is not gated by the node's `Auth` middleware. The middleware only enforces the API key on a hardcoded allow-list of three methods: [5](#0-4) 

`set_commitments` (namespaced RPC method, not `backup_create`/`backup_validate`/`backup_info`) is therefore served to any caller regardless of `rpc.api_key` configuration, since the RPC server wires this same `Auth` layer in front of all merged RPC modules including the batch-prover module: [6](#0-5) 

### Impact Explanation
Once forged commitments are written via `put_commitment_by_index` / `put_commitment_index_by_l1` / `put_prover_pending_commitment`, the batch prover's subsequent proving logic (which reads these same tables via `SequencerCommitmentByIndex`/`ProverPendingCommitments`/`CommitmentIndicesByL1`) can be steered into producing or accepting proofs for a commitment/L2-range that the sequencer never actually published on Bitcoin DA, or into discarding/overwriting a legitimately pending commitment index. This breaks the equality that should hold between "commitment accepted by the batch prover" and "commitment actually posted by the sequencer to DA," i.e., an attacker can get a false state transition proved (or a genuine one blocked) without ever compromising a sequencer key — satisfying the Critical bullet "a false state transition proved or a true one made unprovable" and the High bullet "an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`."

### Likelihood Explanation
The method requires only network access to the batch-prover's JSON-RPC endpoint and a single unauthenticated call; no sequencer/prover/operator key or role is needed to invoke it (the RPC's own logic runs with prover-node privileges, but the caller has none). Given the `Auth` allow-list only covers `backup_*` methods, this is trivially reachable whenever the RPC port is exposed.

### Recommendation
Add `set_commitments` (and other state-mutating batch-prover RPCs) to the `Auth` middleware's protected-method list so they require the configured API key, and additionally validate any externally-provided commitment against the actual DA blob (sender/signature) and against the existing commitment chain (`index` continuity, Merkle root vs. synced L2 blocks) before writing it into `SequencerCommitmentByIndex`/`CommitmentIndicesByL1`/`ProverPendingCommitments`, matching the checks already performed in `da_block_handler.rs`.

### Proof of Concept
1. Start a batch-prover node with default config (no `api_key` set, or any `api_key`, since `set_commitments` is unaffected by it either way).
2. Send an unauthenticated JSON-RPC request:
```json
{"jsonrpc":"2.0","id":1,"method":"setCommitments","params":[[{"l1_height":"0x1","index":"0x2","merkle_root":"0x...","l2_end_block_number":"0x64"}]]}
```
3. Observe via `getCommitmentIndicesByL1` / ledger DB that the forged commitment at attacker-chosen `index`/`l1_height` is now stored and queued as a pending commitment for proving, despite never having been posted by the sequencer on Bitcoin DA. [1](#0-0)

### Citations

**File:** crates/batch-prover/src/rpc.rs (L345-378)
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
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L567-576)
```rust
                DataOnDa::SequencerCommitment(commitment) => {
                    log!("Found sequencer commitment with index {}", commitment.index);
                    if blob.sender().as_ref() != sequencer_da_public_key {
                        log!(
                            "Sequencer commitment sender is not sequencer, wtxid={:?}",
                            blob.wtxid()
                        );
                        continue;
                    }
                    if SequencerCommitmentAccessor::<S>::get(commitment.index, &mut working_set)
```

**File:** crates/fullnode/src/da_block_handler.rs (L482-508)
```rust
        // Determine the starting L2 height for this commitment
        // For first commitment (index 1), start at Tangerine fork height
        // Otherwise, start at previous commitment's end height + 1
        let start_l2_height = if sequencer_commitment.index == 1 {
            get_tangerine_activation_height_non_zero()
        } else {
            match self
                .ledger_db
                .get_commitment_by_index(sequencer_commitment.index - 1)?
            {
                Some(previous_commitment) => previous_commitment.l2_end_block_number + 1,
                None => {
                    // If previous commitment is missing, store this one as pending
                    info!(
                            "Commitment with index {} is missing its predecessor (index {}). Storing as pending.",
                            sequencer_commitment.index,
                            sequencer_commitment.index - 1
                        );
                    insert_pending_commitment_if_not_exists(
                        &self.ledger_db,
                        sequencer_commitment,
                        found_in_l1_block_height,
                    )?;
                    return Ok(ProcessingResult::Pending);
                }
            }
        };
```

**File:** crates/fullnode/src/da_block_handler.rs (L550-578)
```rust
        // Halt processing if merkle root doesn't match
        if l2_blocks_tree.root() != Some(sequencer_commitment.merkle_root) {
            return Err(
                HaltingError::Commitment(CommitmentError::MerkleRootMismatch(format!(
                    "Merkle root mismatch - expected 0x{} but got 0x{}. Skipping commitment.",
                    hex::encode(
                        l2_blocks_tree
                            .root()
                            .ok_or(anyhow!("Could not calculate l2 block tree root"))?
                    ),
                    hex::encode(sequencer_commitment.merkle_root)
                )))
                .into(),
            );
        }

        // Store the commitment and update all related state
        self.ledger_db.update_commitments_on_da_slot(
            found_in_l1_block_height,
            sequencer_commitment.clone(),
        )?;

        self.ledger_db.set_l2_range_by_commitment_merkle_root(
            sequencer_commitment.merkle_root,
            (L2BlockNumber(start_l2_height), L2BlockNumber(end_l2_height)),
        )?;

        self.ledger_db
            .put_commitment_by_index(sequencer_commitment)?;
```

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
