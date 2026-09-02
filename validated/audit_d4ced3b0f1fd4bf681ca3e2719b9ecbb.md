No vulnerability found for this question.

**Reasoning summary:** `create_rpc_methods` in `bin/citrea/src/rollup/bitcoin.rs` (lines 69-104) contains no guest-method-id selection logic whatsoever — it only merges JSON-RPC modules (`CitreaRuntime`, ledger, backup, DA) conditionally on `node_type`. [1](#0-0) 

The two places that actually pick a "method id"/ELF are unrelated to `create_rpc_methods` and are both deterministic, immutable functions of L2 height, not of RPC concurrency:

1. **Prover side**: `get_batch_proof_elfs`/`get_batch_proof_code_commitments` build a static `HashMap<SpecId, _>` once at node startup from compile-time guest tables; at proving time `start_proving` looks up the ELF via `fork_from_block_number(end_l2_height).spec_id`, a pure function of the partition's `end_l2_height`. [2](#0-1) [3](#0-2) 
2. **Light-client verifier side**: `BatchProofMethodIdAccessor` stores `(activation_l2_height, method_id)` pairs in JMT state, updatable only via security-council-signed `DataOnDa::BatchProofMethodId` DA transactions (3-of-5 threshold, chain-id checked, monotonic activation height enforced), and `process_complete_proof` selects the id via binary search on `batch_proof_output_last_l2_height` — the L2 height actually committed to by the proof's journal, not by wall-clock concurrency. [4](#0-3) [5](#0-4) 

Both sides key off the same L2-height axis and neither is mutated or raced by concurrent proving sessions or by an unprivileged party's JSON-RPC/EVM/deposit traffic; the elfs/code-commitments maps are built once and read-only thereafter, and the light-client's activation table only advances via signed council transactions unrelated to `create_rpc_methods`. No equality between "id used to prove" and "id verified against" is shown to diverge under the described load — the premise that `create_rpc_methods` participates in method-id selection does not hold in this codebase.

### Citations

**File:** bin/citrea/src/rollup/bitcoin.rs (L68-104)
```rust
    #[instrument(level = "trace", skip_all, err)]
    fn create_rpc_methods(
        &self,
        node_type: NodeType,
        storage: ProverStorage,
        ledger_db: &LedgerDB,
        da_service: &Arc<Self::DaService>,
        backup_manager: &Arc<BackupManager>,
        rpc_config: RpcConfig,
    ) -> Result<jsonrpsee::RpcModule<()>, anyhow::Error> {
        let mut rpc_methods = RpcModule::new(());

        if !matches!(node_type, NodeType::LightClientProver) {
            let methods = <CitreaRuntime<DefaultContext, Self::DaSpec>>::rpc_methods(
                storage,
                ledger_db.clone(),
            );

            rpc_methods.merge(methods)?;
        }

        let ledger_db_methods = sov_ledger_rpc::server::create_rpc_module::<LedgerDB>(
            ledger_db.clone(),
            rpc_config.into(),
        );
        rpc_methods.merge(ledger_db_methods)?;

        let backup_methods = create_backup_rpc_module(ledger_db.clone(), backup_manager.clone());
        rpc_methods.merge(backup_methods)?;

        if matches!(node_type, NodeType::BatchProver) || matches!(node_type, NodeType::Sequencer) {
            let da_methods = create_da_rpc_module(da_service.clone());
            rpc_methods.merge(da_methods)?;
        }

        Ok(rpc_methods)
    }
```

**File:** bin/citrea/src/rollup/bitcoin.rs (L199-222)
```rust
    fn get_batch_proof_elfs(&self) -> HashMap<SpecId, Vec<u8>> {
        match self.network {
            Network::Mainnet => BATCH_PROOF_MAINNET_GUESTS
                .iter()
                .map(|(k, (_, code))| (*k, code.clone()))
                .collect(),
            Network::Testnet => BATCH_PROOF_TESTNET_GUESTS
                .iter()
                .map(|(k, (_, code))| (*k, code.clone()))
                .collect(),
            Network::Devnet => BATCH_PROOF_DEVNET_GUESTS
                .iter()
                .map(|(k, (_, code))| (*k, code.clone()))
                .collect(),
            Network::Nightly => BATCH_PROOF_LATEST_BITCOIN_GUESTS
                .iter()
                .map(|(k, (_, code))| (*k, code.clone()))
                .collect(),
            Network::TestNetworkWithForks => BATCH_PROOF_REGTEST_BITCOIN_GUESTS
                .iter()
                .map(|(k, (_, code))| (*k, code.clone()))
                .collect(),
        }
    }
```

**File:** crates/batch-prover/src/prover.rs (L662-683)
```rust
        let end_l2_height = input
            .sequencer_commitments
            .last()
            .expect("Must have 1")
            .l2_end_block_number;
        let current_spec = fork_from_block_number(end_l2_height).spec_id;

        let elf = self
            .elfs_by_spec
            .get(&current_spec)
            .expect("Every fork should have an elf attached")
            .clone();

        tracing::info!("Starting proving with ELF of spec: {:?}", current_spec);

        let input = borsh::to_vec(&input.into_v3_parts()).expect("Input serialization cannot fail");

        let proof_data = ProofData {
            input,
            assumptions: vec![],
            elf,
        };
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L288-303)
```rust
        let batch_proof_method_ids = BatchProofMethodIdAccessor::<S>::get(working_set)
            .expect("Batch proof method ids must exist");

        let batch_proof_method_id = if batch_proof_method_ids.len() == 1 {
            batch_proof_method_ids[0].1
        } else {
            let idx = match batch_proof_method_ids
                // Returns err and the index to be inserted, which is the index of the first element greater than the key
                // That is why we need to subtract 1 to get the last element smaller than the key
                .binary_search_by_key(&batch_proof_output_last_l2_height, |(height, _)| *height)
            {
                Ok(idx) => idx,
                Err(idx) => idx.saturating_sub(1),
            };
            batch_proof_method_ids[idx].1
        };
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-565)
```rust
                DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
                    log!("Found batch proof method id");
                    let batch_proof_method_ids =
                        BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();

                    let last_activation_height = batch_proof_method_ids
                        .last()
                        .expect("Should be at least one")
                        .0;

                    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
                        log!("Batch proof method id activation height is not greater than the last one");
                        continue;
                    }

                    let circuit_chain_id = citrea_network_to_chain_id(network);
                    if circuit_chain_id != batch_proof_method_id.body.chain_id {
                        log!("Method ID upgrade transactions chain ID does not match circuit chain ID");
                        continue;
                    }

                    // Verify the signatures only if the activation height is greater than the last one
                    // This prevents replay attacks of old method IDs
                    if !verify_method_id_security_council(
                        *method_id_upgrade_authority_da_public_keys,
                        batch_proof_method_id.body.serialize().as_slice(),
                        batch_proof_method_id.signatures_with_index(),
                    ) {
                        log!("Method ID security council verification failed");
                        continue;
                    }

                    BatchProofMethodIdAccessor::<S>::insert(
                        batch_proof_method_id.body.activation_l2_height,
                        batch_proof_method_id.body.method_id,
                        &mut working_set,
                    );
```
