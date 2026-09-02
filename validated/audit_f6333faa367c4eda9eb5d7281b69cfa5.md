### Title
Unauthenticated `batchProver_setCommitments` RPC lets any caller overwrite sequencer commitments in the batch prover's ledger, bypassing DA/signature verification - ([File: crates/batch-prover/src/rpc.rs])

### Summary
The `VaultRegistry` finding shows a class of bug where a privileged actor can register a trusted artifact through a side-channel that bypasses the sole intended validation path (the approved `VaultFactory`). The equivalent binding in Citrea is: *a `SequencerCommitment` accepted by the batch prover must equal a commitment that was actually posted to Bitcoin DA and signed by the sequencer's DA key* (this is what `extract_relevant_sequencer_commitments` / the light-client circuit enforce via `blob.sender().as_ref() != sequencer_da_public_key` checks). The `batchProver_setCommitments` JSON-RPC method breaks this binding: it writes attacker-supplied `SequencerCommitment` records directly into the batch prover's ledger DB with no DA inclusion proof, no signature check, and — critically — no API-key/authentication requirement, since it is absent from the `PROTECTED_METHODS` list enforced by the `Auth` RPC middleware.

### Finding Description
The batch prover exposes `setCommitments` as part of its RPC trait: [1](#0-0) 

Its server implementation writes the caller-supplied commitment straight into local storage, without verifying that it was ever posted on Bitcoin DA or signed by the sequencer's DA private key: [2](#0-1) 

Compare this to the only other path by which a `SequencerCommitment` is meant to become trusted — DA extraction, which requires the blob to be signed by the sequencer's DA public key: [3](#0-2) [4](#0-3) 

The node's RPC authentication layer only protects three named methods (`backup_create`, `backup_validate`, `backup_info`) with an API key; every other method, including `batchProver_setCommitments`, is passed through unauthenticated: [5](#0-4) [6](#0-5) 

This is the direct analog of the `VaultsRegistry.addVault` issue: just as the registry owner can register a vault that was never deployed by an approved factory (undermining the "only factory-created vaults are trusted" guarantee), any unauthenticated RPC caller can inject a `SequencerCommitment` into the batch prover's trusted local state that was never posted to DA nor signed by the sequencer (undermining the "only DA-verified, sequencer-signed commitments are trusted" guarantee). The doc comment itself acknowledges the danger ("overrides the commitment already if exists, so use with caution"), mirroring the "Acknowledged - By Design" resolution in the source report, but here the bypass is not gated behind any privileged role at all.

### Impact Explanation
This matches the "High" impact category explicitly listed in scope: *"an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`."* Any network caller reaching the batch prover's RPC port can overwrite `put_commitment_by_index`, `put_commitment_index_by_l1`, and `put_prover_pending_commitment` for an arbitrary commitment index with an arbitrary `merkle_root` / `l2_end_block_number`, corrupting the prover's local view of which L2 ranges/commitments are pending proof. Because `set_commitments` is followed by `prove`/`createCircuitInput`, an attacker can drive the batch prover to attempt building proofs (or generate proving inputs) over forged commitment data that never existed on Bitcoin DA, without needing the sequencer's key, a security-council signature, or any operator credential — a state mutation an unprivileged network caller should never be able to trigger.

### Likelihood Explanation
Likelihood is high for reaching the code path: the RPC method requires no authentication token, no role check, and no proof that the commitment exists on DA; a single JSON-RPC call is sufficient. The only precondition is network access to the batch prover's RPC endpoint (same precondition as any other unauthenticated RPC call, which is explicitly in-scope per the rules).

### Recommendation
Add `batchProver_setCommitments` (and similarly powerful debug/admin RPC methods that mutate ledger state, such as `submitFakeProof`) to the `PROTECTED_METHODS` list in `crates/common/src/rpc/auth.rs` so they require the configured API key, or remove/gate them behind a build-time "debug only" feature flag so they cannot be reached on production deployments.

### Proof of Concept
1. Start a batch prover node with default configuration (no `api_key` set, or an attacker without the key but with network access to the RPC port).
2. Send: `curl -d '{"jsonrpc":"2.0","id":1,"method":"batchProver_setCommitments","params":[[{"merkleRoot":"0x<forged>","index":1,"l1Height":1,"l2EndBlockNumber":10}]]}' http://<batch-prover-rpc>`.
3. Observe (via `crates/batch-prover/src/rpc.rs::set_commitments`, lines 345-378) that the forged commitment is written to the ledger DB with no DA inclusion or sequencer-signature check, unlike the legitimate DA-scan path in `crates/light-client-prover/src/circuit/mod.rs` (lines 567-585) which requires `blob.sender().as_ref() == sequencer_da_public_key`.
4. Call `batchProver_prove` / `batchProver_createInput` and observe the prover operating over the injected, unverified commitment.

### Citations

**File:** crates/batch-prover/src/rpc.rs (L171-178)
```rust
pub trait BatchProverRpc {
    /// Manually set commitments. It overrides the commitment already if exists, so use with caution.
    ///
    /// # Arguments
    /// * `commitments` - A vector of sequencer commitments to be set.
    #[method(name = "setCommitments")]
    async fn set_commitments(&self, commitments: Vec<SequencerCommitmentRpcParam>)
        -> RpcResult<()>;
```

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

**File:** crates/light-client-prover/src/circuit/mod.rs (L567-585)
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
                        .is_none()
                    {
                        SequencerCommitmentAccessor::<S>::insert(
                            commitment.index,
                            commitment,
                            &mut working_set,
                        )
                    }
                }
```

**File:** crates/bitcoin-da/src/verifier.rs (L169-178)
```rust
                    ParsedTransaction::SequencerCommitment(seq_comm) => {
                        if let Some(hash) = seq_comm.get_sig_verified_hash() {
                            blobs.push(BlobWithSender::new(
                                seq_comm.body,
                                seq_comm.public_key,
                                hash,
                                *wtxid,
                            ));
                        }
                    }
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
