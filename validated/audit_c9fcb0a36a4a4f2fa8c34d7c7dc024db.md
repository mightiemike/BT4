### Title
Unauthenticated `batchProver_submitFakeProof` JSON-RPC method lets any caller submit a `FakeReceipt` to DA that will be accepted by the light-client circuit — ([File: crates/batch-prover/src/rpc.rs])

### Summary
The Auth middleware only gates three hard-coded RPC methods, and every other RPC method registered on the same public jsonrpsee server — including the batch-prover's state-mutating methods — is reachable by anyone who can reach the node's RPC port, without an API key.

### Finding Description
`Auth::call` only checks `req.method_name()` against a fixed allowlist of `PROTECTED_METHODS = ["backup_create", "backup_validate", "backup_info"]`; every other method bypasses the API-key check entirely and is dispatched straight to the underlying service. [1](#0-0) 

This `Auth` middleware wraps the *single* RPC server that all modules merge their RPC methods into (`start_rpc_server` builds one `RpcServiceBuilder` layer for the whole `RpcModule<()>`), so there is no separate "admin" listener — the batch-prover's own RPC trait is merged into the exact same public module. [2](#0-1) [3](#0-2) 

Among the methods exposed this way is `batchProver_submitFakeProof`, whose implementation builds a `BatchProofCircuitOutputV3` from real ledger data, wraps it in a `risc0_zkvm::FakeReceipt`/`InnerReceipt::Fake`, serializes it, and sends it to DA via `DaTxRequest::ZKProof`: [4](#0-3) 

The doc comment for this endpoint even states its intended purpose is to "Simulate proving ... and submit the fake proof to DA" for testing, i.e. it is meant to be an operator/test-only tool, not a public production capability. [5](#0-4) 

Likewise `batchProver_setCommitments` unconditionally overwrites stored `SequencerCommitment`s in the ledger DB ("overrides the commitment already if exists, so use with caution") purely based on caller-supplied RPC parameters, with no authentication: [6](#0-5) 

This is the structural analog of the reported Martian bug: in the original report, an internal/privileged message channel (`martian_contentscript_background_channel`, and by extension internal `onmessage` channels like `aebc`) was reachable by an untrusted caller because the code that was supposed to gate access to privileged channels didn't actually restrict which channel/handler an attacker-controlled message could reach. Here, the `Auth` gate that is supposed to restrict privileged/state-mutating RPC "channels" (methods) to an authenticated caller only enumerates 3 method names, leaving every other privileged method — including one whose entire purpose is to inject a fake but well-formed proof receipt into the DA pipeline — reachable without any credential.

### Impact Explanation
This crosses the "proof journal vs. what actually happened" / "light client proof split across honest provers" / "false state transition proved" boundary explicitly called out as Critical: whether the light-client prover's guest actually verifies the fake receipt's signature/seal (risc0 `FakeReceipt` verification is normally gated by a "dev mode"/`RISC0_DEV_MODE`-style flag) determines whether this fake proof would actually be treated as a valid ZK proof downstream. Regardless of that downstream gate, at minimum this is a High-severity finding on its own: `batchProver_submitFakeProof` and `batchProver_setCommitments` are unauthenticated JSON-RPC calls that mutate node/ledger state (write commitments, submit DA transactions carrying receipt data) and bypass the `Auth` middleware entirely, exactly matching the "unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`" High-impact category. If risc0's fake-receipt verification path is enabled/relied upon in the deployed light-client-prover configuration, this could escalate to Critical (false state transition proved / accepted commitment without the required security-council or key-holder authorization).

### Likelihood Explanation
No special role, key, or privileged network position is required — the attacker only needs to be able to send a JSON-RPC request to the node's exposed RPC endpoint, which is the normal way any client interacts with a Citrea node. The only variable is whether the operator has configured an `api_key` at all — but because `PROTECTED_METHODS` hard-codes just the backup methods, even an operator who *has* configured an API key gets no protection at all on the batch-prover's `setCommitments`/`submitFakeProof`/`prove`/`retryProvingJob` methods, since `Auth::call` never even considers `api_key` unless the method name matches the allowlist.

### Recommendation
Change the `Auth` middleware's model from a hard-coded allowlist of "protected" methods to an allowlist of "public" methods, denying/gating everything else by default (or explicitly add every state-mutating batch-prover/light-client-prover admin RPC method — `setCommitments`, `prove`, `submitFakeProof`, `pauseProving`, `retryProvingJob`, `createCircuitInput`, etc. — to `PROTECTED_METHODS`), and require the API key unconditionally for any method that writes to the ledger DB or submits DA transactions.

### Proof of Concept
1. Start a batch-prover node with default RPC config (API key set or not — it does not matter for this method).
2. From any unauthenticated client, call:
```json
{"jsonrpc":"2.0","id":1,"method":"batchProver_setCommitments","params":[[{ "merkleRoot": "0x...", "index": "0x5", "l2EndBlockNumber": "0x64", "l1Height": "0x1" }]]}
```
This overwrites the stored commitment at index 5 without any credential, per `set_commitments` [6](#0-5) .
3. Alternatively call `batchProver_submitFakeProof` with an `index_start`/`index_end` range that exists in the ledger DB; the node will construct a `FakeReceipt` from real ledger data and submit it to DA, all without authentication, per `submit_fake_proof` [7](#0-6) .
4. Confirm via `Auth::call` that neither `batchProver_setCommitments` nor `batchProver_submitFakeProof` appears in `PROTECTED_METHODS`, so no API key check is ever performed for these calls [1](#0-0) .

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

**File:** crates/common/src/rpc/server.rs (L42-57)
```rust
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);

    task_executor.spawn_with_signal(move |cancellation_token| {
        async move {
            let server = ServerBuilder::default()
                .max_connections(max_connections)
                .max_subscriptions_per_connection(max_subscriptions_per_connection)
                .max_request_body_size(max_request_body_size)
                .max_response_body_size(max_response_body_size)
                .set_batch_request_config(BatchRequestConfig::Limit(batch_requests_limit))
                .set_http_middleware(middleware)
                .set_rpc_middleware(rpc_middleware)
                .build([listen_address].as_ref())
```

**File:** crates/batch-prover/src/lib.rs (L136-144)
```rust
    let rpc_context = rpc::create_rpc_context::<_, _, Vm>(
        ledger_db.clone(),
        request_tx,
        da_service.clone(),
        storage_manager.clone(),
        code_commitments.clone(),
        rpc_config.clone(),
    );
    let rpc_module = rpc::register_rpc_methods(rpc_context, rpc_module)?;
```

**File:** crates/batch-prover/src/rpc.rs (L187-200)
```rust
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

**File:** crates/batch-prover/src/rpc.rs (L405-525)
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

        let storage = self
            .context
            .storage_manager
            .create_storage_for_l2_height(last_l2_block.height + 1);
        let last_l1_hash_on_contract = get_last_l1_hash_on_contract::<DefaultContext>(
            Default::default(),
            storage,
            &mut Default::default(),
            [0; 32],
        );

        let output = BatchProofCircuitOutput::V3(BatchProofCircuitOutputV3 {
            state_roots,
            final_l2_block_hash: last_l2_block.hash,
            state_diff: cumulative_state_diff,
            last_l2_height: last_l2_block.height,
            sequencer_commitment_hashes,
            sequencer_commitment_index_range: (index_start, index_end),
            last_l1_hash_on_bitcoin_light_client_contract: last_l1_hash_on_contract,
            previous_commitment_index: Some(previous_commitment.index),
            previous_commitment_hash: Some(previous_commitment.serialize_and_calculate_sha_256()),
        });

        let output_serialized = borsh::to_vec(&output).expect("Output serialization cannot fail");

        let spec_id = fork_from_block_number(last_l2_block.height).spec_id;
        let method_id: [u32; 8] = self
            .context
            .code_commitments
            .get(&spec_id)
            .expect("Spec for L2 block must exist")
            .clone()
            .into();

        let claim = MaybePruned::Value(ReceiptClaim::ok(method_id, output_serialized));
        let fake_receipt = FakeReceipt::new(claim);
        // Receipt with verifiable claim
        let receipt = InnerReceipt::Fake(fake_receipt);
        let proof = bincode::serialize(&receipt).expect("Receipt serialization cannot fail");

        let tx_id = self
            .context
            .da_service
            .send_transaction(DaTxRequest::ZKProof(proof.clone()))
            .await
            .map_err(internal_rpc_error)?;
```
