Based on my investigation, the strongest analog I found is an unauthenticated JSON-RPC surface on the batch prover that lets any caller reaching that RPC port overwrite the sequencer-commitment bookkeeping the batch prover uses as ground truth — structurally the same defect class as the report: a privileged-sounding operation (`updateLoanParams` swapping the oracle) that lacks a binding check against the value that was actually agreed to (here, the commitment that was actually posted/verified by the sequencer on DA). However, I was not able to fully confirm within the remaining budget whether `set_commitments`/`submit_fake_proof` in `crates/batch-prover/src/rpc.rs` are gated by a separate transport-level protection (e.g., only bound to a local/test-only socket) versus being exposed on the same JSON-RPC server as other methods and only excluded from the `PROTECTED_METHODS` list in `crates/common/src/rpc/auth.rs`.

### Title
Unauthenticated `set_commitments`/`submit_fake_proof` RPCs let any caller overwrite the batch prover's sequencer-commitment ground truth - (File: crates/batch-prover/src/rpc.rs)

### Summary
The `Auth` middleware only gates three RPC methods (`backup_create`, `backup_validate`, `backup_info`) behind an API key; every other JSON-RPC method, including the batch-prover's `set_commitments` and `submit_fake_proof`, is reachable by any caller with network access to the RPC port. `set_commitments` writes attacker-supplied `SequencerCommitment` values (merkle root, index, l2 end height) directly into the ledger DB tables (`put_commitment_by_index`, `put_commitment_index_by_l1`, `put_prover_pending_commitment`) that the batch prover later trusts as the basis for `create_circuit_input`/`submit_fake_proof`, with no signature or DA-inclusion check.

### Finding Description
`crates/common/src/rpc/auth.rs` defines:
```
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];
``` [1](#0-0) 
and only requests for these three names are checked against an API key; all other methods bypass the check entirely and are dispatched directly: `if !PROTECTED_METHODS.contains(&method) { return Box::pin(service.call(req)); }` [2](#0-1) .

`BatchProverRpcServer::set_commitments` (exposed as `setCommitments`) takes a caller-supplied vector of `SequencerCommitmentRpcParam` and, per commitment, calls `put_commitment_by_index`, `put_commitment_index_by_l1`, and `put_prover_pending_commitment` on the ledger DB — with no verification that the commitment was actually posted by the sequencer on Bitcoin, matched by signature, or consistent with any previously stored commitment for that index: [3](#0-2) . This mirrors the report's root cause exactly: a value (`cur.oracle` in the report, here the on-chain-verified sequencer commitment) that should be immutable once established can be silently replaced by supplying a "new" version through an endpoint that never checks it against the value that was actually authorized.

`submit_fake_proof` (exposed as `submitFakeProof`) then reads back `get_commitment_by_index` for the (now attacker-controlled) index range, builds `initial_state_root`/`state_roots` from the ledger DB, and produces a `BatchProofResponse` used by downstream logic as if it were a real proof [4](#0-3) , compounding the effect of the forged commitments.

### Impact Explanation
If this RPC surface is reachable without additional network-layer restriction, an unprivileged caller can rewrite the sequencer-commitment index used by the batch prover as its source of truth for `initial_state_root`s and commitment ranges, and can then request a "fake proof" over that forged range. This matches the "High" bar in the rules ("an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`"): node state (`ledger_db` commitment tables) is mutated by an unauthenticated caller, potentially causing the batch prover to diverge from the sequencer commitments actually posted on Bitcoin.

### Likelihood Explanation
Likelihood depends entirely on deployment: if the batch prover's RPC endpoint is bound to a public/production-facing interface with the same `Auth` middleware as other node RPCs, this is directly and trivially exploitable by any network caller. If, as is common for debug/test-only RPCs, this endpoint is only ever enabled on a private/loopback interface in production configurations, the rules' exclusion ("Reject analogs that depend on a deployment ignoring the documented configuration") would apply and this finding would not qualify. I could not verify from the available context which of these is the documented production configuration, so this is flagged as uncertain rather than conclusively in- or out-of-scope.

### Recommendation
Add `set_commitments` and `submit_fake_proof` (and any other state-mutating batch-prover RPC methods) to `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs`, or otherwise gate them so they require the same API-key/authentication as `backup_create`/`backup_validate`, and additionally validate that any commitment passed to `set_commitments` corresponds to a commitment actually observed on DA (matching signature and inclusion) rather than being accepted unconditionally.

### Proof of Concept
1. Start a `batch-prover` node with its RPC server exposed (as configured by the node operator).
2. As an unauthenticated client, call `setCommitments` with an arbitrary `SequencerCommitmentRpcParam` (arbitrary `merkle_root`, `index`, `l2_end_block_number`) for an index range not actually posted by the sequencer.
3. Observe that `crates/batch-prover/src/rpc.rs::set_commitments` writes this into `ledger_db` via `put_commitment_by_index`/`put_commitment_index_by_l1`/`put_prover_pending_commitment` without any check against real DA data [3](#0-2) .
4. Call `submitFakeProof` for that index range; the server reads back the forged commitments and builds a `BatchProofResponse` from them [5](#0-4) .

Because I could not confirm the network-exposure/config assumptions for this RPC in the time available, I recommend a follow-up Devin session (with terminal/filesystem access) to check the default `rpc_config` binding for the batch-prover RPC server and whether these specific method names are intended to be internal-only, before treating this as confirmed exploitable in production.

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-11)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];
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

**File:** crates/batch-prover/src/rpc.rs (L405-478)
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

        let previous_commitment = ledger_db
            .get_commitment_by_index(index_start - 1)
            .map_err(internal_rpc_error)?
            .ok_or_else(|| internal_rpc_error("Missing previous commitment index"))?;

        let commitments = ledger_db
            .get_commitment_by_range(index_start..=index_end)
            .map_err(internal_rpc_error)?;
        if commitments.len() as u32 != index_end - index_start + 1 {
            return Err(internal_rpc_error(
                "Missing some commitment indices from the range",
            ));
        }

        let last_commitment = commitments.last().expect("Already ensured");
        let last_l2_block = ledger_db
            .get_l2_block_by_number(&L2BlockNumber(last_commitment.l2_end_block_number))
            .map_err(internal_rpc_error)?
            .ok_or_else(|| internal_rpc_error("Not synced up to latest L2 block yet"))?;

        let initial_state_root = ledger_db
            .get_l2_state_root(previous_commitment.l2_end_block_number)
            .map_err(internal_rpc_error)?
            .expect("Initial L2 state root must exist");

        let mut start_l2_height = previous_commitment.l2_end_block_number + 1;
        let mut sequencer_commitment_hashes = Vec::with_capacity(commitments.len());
        let mut state_roots = Vec::with_capacity(commitments.len() + 1);
        state_roots.push(initial_state_root);

        let mut cumulative_state_diff = CumulativeStateDiff::new();
        for commitment in commitments.iter() {
            let end_l2_height = commitment.l2_end_block_number;

            for l2_height in start_l2_height..=end_l2_height {
                let state_diff = ledger_db
                    .get_l2_state_diff(L2BlockNumber(l2_height))
                    .map_err(internal_rpc_error)?
                    .expect("L2 state diff must exist");
                cumulative_state_diff.extend(state_diff);
            }

            sequencer_commitment_hashes.push(commitment.serialize_and_calculate_sha_256());

            let end_state_root = ledger_db
                .get_l2_state_root(end_l2_height)
                .map_err(internal_rpc_error)?
                .expect("L2 state root must exist");
            state_roots.push(end_state_root);

            start_l2_height = end_l2_height + 1;
        }
```
