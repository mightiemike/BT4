### Title
Reusable, unbound proof facts allow submission of another user's client-side proof without performing computation - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo])

### Summary
The sequencer's "client-side proving" feature for Invoke V3 transactions accepts a `(proof_facts, proof)` pair that is verified once and then trusted. The on-chain/OS-side validation of the proof facts (`check_proof_facts` / `validate_proof_facts`) checks only structural/global properties — proof variant, program hash allow-list, proof version, and that the referenced block number/hash/config hash are valid and not too recent — but never binds the proof to the specific transaction's `sender_address`, `nonce`, or `calldata`. This mirrors the reported audit-class bug: any party can copy a previously-published, validly-verified `proof_facts`/`proof` pair (visible in mempool/consensus broadcasts, in `CentralInvokeTransactionV3`, or in historical blocks) and attach it to a *different* transaction, "proving" work they never performed.

### Finding Description
The proof-facts validation performed by the sequencer (both in the Cairo OS constraints and in the Rust blockifier pre-validation) is: [1](#0-0) 

and the corresponding Rust-side check: [2](#0-1) 

Neither routine incorporates `sender_address`, `nonce`, or `calldata` into what is checked against the proof. The only cryptographic binding enforced is `verify_proof`, which reconstructs an output preimage purely from `proof_facts` content (program hash, block hash/number, config hash) and checks the SNARK against that preimage: [3](#0-2) 

Because `proof_facts`/`proof` do not commit to the specific transaction issuing them, and because the proof-manager's `contains_proof`/`set_proof` keys purely off `proof_facts.hash()` (a hash of block/program/config data, not of the transaction), a previously verified and stored proof can be attached, unmodified, to an entirely different transaction (different sender, nonce, calldata) that also passes `check_proof_facts`/`validate_proof_facts`. The transaction hash does technically differ (since `proof_facts` is folded into `compute_invoke_transaction_hash`), so this is not exactly a duplicate-transaction replay, but the *proof itself*, which is supposed to represent client-side proving work specific to that sender/action, is fully reusable by any other account without performing any actual proving: [4](#0-3) 

The gateway/transaction-converter flow explicitly optimizes for and assumes proofs can be “already known” (`contains_proof`) and skips reverification when the same `proof_facts` were seen before — this is a legitimate performance optimization for the *same* transaction being re-verified, but there is no mechanism preventing a *different* transaction from supplying the same `proof_facts`/`proof` and reusing this cached "already verified" state: [5](#0-4) 

### Impact Explanation
Any unprivileged sender can craft an Invoke V3 transaction with `proof_facts`/`proof` copied verbatim from a transaction they observed (in the gossiped mempool, in the CENDE central objects blob, or in a historical block), attach their own `sender_address`/`nonce`/`calldata`, sign it, and have the sequencer accept it as though they had performed the associated client-side proving work. Since `check_proof_facts`/`validate_proof_facts` never checks that the proof was produced by or for the submitting account, the sequencer’s "proof of work" guarantee for client-side proving is broken for every account, undermining whatever privacy/anti-spam/fee-discount or off-chain-computation guarantee the client-side proving feature is meant to provide. This is a protocol-level correctness violation (unauthorized use of another party's computational proof) reachable from a single submitted transaction by an unprivileged sender — no operator, prover, or node privilege required.

### Likelihood Explanation
High. `proof_facts` and `proof` are transmitted in the clear as part of the transaction body (gateway RPC, P2P/consensus broadcast, and CENDE central objects), so any observer can copy them. No signature or transaction-specific commitment ties the proof to the original sender's identity, nonce, or calldata. Constructing the malicious transaction requires only standard RPC access to submit an Invoke V3 transaction with these copied fields.

### Recommendation
Bind the proof facts cryptographically to the specific transaction context before verification is accepted — e.g., include a hash/commitment of the submitting `sender_address` (and/or `nonce`/`calldata`) as part of the data that must be proven/verified inside `verify_proof`/`reconstruct_output_preimage`, or require the account's signature to cover the `proof_facts` in a way that ties the proof-manager entry to a single sender. Additionally, consider scoping `contains_proof`/`set_proof` keys to include the sender address so a cached "already verified" proof cannot be reused by an unrelated account.

### Proof of Concept
1. Observe any Invoke V3 transaction on the network (e.g., in gossip, in a `CentralInvokeTransactionV3` CENDE blob, or in a historical block) that carries non-empty `proof_facts` and `proof` — e.g., the fixture data in `crates/apollo_consensus_orchestrator/resources/central_invoke_tx_client_side_proving.json`.
2. Extract the `proof_facts` array and `proof` bytes verbatim.
3. Craft a new `RpcInvokeTransactionV3` with attacker-controlled `sender_address`, `nonce`, and `calldata`, but reuse the copied `proof_facts`/`proof` unchanged.
4. Submit via the gateway. `validate_proof_facts` in `crates/blockifier/src/transaction/account_transaction.rs` only checks block number/hash/program hash/config hash — all of which remain valid/unchanged from the original proof — so the check passes. `verify_proof` succeeds since it verifies against the same (unmodified) `proof_facts` preimage.
5. The transaction is accepted into the mempool and eventually included in a block, despite the attacker never having performed the client-side proving computation the `proof` was originally generated for.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L33-82)
```text
// Validates that the proof facts of an invoke transaction are of a valid virtual OS run.
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);

    // Validate the proof header.
    static_assert ProofHeader.SIZE == 3;
    let proof_header = cast(proof_facts, ProofHeader*);
    assert proof_header.proof_variant = VIRTUAL_SNOS;
    assert is_program_hash_allowed(proof_header.program_hash) = TRUE;
    // Only proof version V1 is accepted.
    with_attr error_message("Unsupported proof version") {
        assert proof_header.proof_version = PROOF_VERSION_V1;
    }

    // Validate the virtual OS output header.
    let os_output_header = cast(&proof_facts[ProofHeader.SIZE], VirtualOsOutputHeader*);

    with_attr error_message("Virtual OS output version is not supported") {
        assert os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION;
    }

    // Validate that the proof facts block number is not too recent.
    // (This is a sanity check - the following non-zero check ensures that the block hash is
    // not trivial).
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
    // Not all block hashes are stored in the contract; Make sure the requested one is not trivial.
    assert_not_zero(os_output_header.base_block_hash);

    // validate that the proof facts block hash is the true hash of the proof facts block number.
    read_block_hash_from_storage(
        block_number=os_output_header.base_block_number,
        expected_block_hash=os_output_header.base_block_hash,
    );

    // validate that the proof facts config hash is the true hash of the OS config.
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

    return ();
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L103-157)
```rust
/// Reconstructs the output preimage from proof facts for circuit verification.
///
/// Proof facts layout: `[PROOF_VERSION_V*, variant, program_hash, ...task_output]`
/// Output preimage layout: `[num_tasks=1, output_size, program_hash, ...task_output]`
/// where `output_size = task_content.len() + 1` (includes itself).
pub fn reconstruct_output_preimage(
    proof_facts: &ProofFacts,
) -> Result<Vec<Felt>, VerifyProofError> {
    // Proof facts must contain at least [PROOF_VERSION_V*, variant, program_hash].
    if proof_facts.0.len() < 3 {
        return Err(VerifyProofError::ProofFactsTooShort { length: proof_facts.0.len() });
    }
    // Skip PROOF_VERSION_V* (index 0) and variant (index 1).
    let task_content = &proof_facts.0[2..];
    let output_size = Felt::from(
        u64::try_from(task_content.len() + 1).expect("task content length exceeds u64::MAX"),
    );
    Ok([Felt::ONE, output_size].into_iter().chain(task_content.iter().copied()).collect())
}

/// Verifies a submitted proof against the proof facts using the circuit verifier.
///
/// The first element of `proof_facts` must be V1, verified via `privacy-circuit-verify-v1`.
pub fn verify_proof(proof_facts: ProofFacts, proof: Proof) -> Result<(), VerifyProofError> {
    // Reject empty proof payloads before running the verifier.
    if proof.is_empty() {
        return Err(VerifyProofError::EmptyProof);
    }

    let proof_version_felt = proof_facts.0.first().copied().unwrap_or_default();
    let proof_version = ProofVersion::try_from(proof_version_felt)
        .map_err(|()| VerifyProofError::InvalidProofVersion { actual: proof_version_felt })?;

    let output_preimage = reconstruct_output_preimage(&proof_facts)?;
    // TODO(Avi): Avoid cloning the proof.
    let proof_bytes = proof.0.to_vec();

    match proof_version {
        // V0 proofs are no longer verifiable: the v0 circuit was removed. V0 proof facts are only
        // tolerated by the blockifier (gated per protocol version) for replaying historical blocks.
        ProofVersion::V0 => {
            return Err(VerifyProofError::InvalidProofVersion { actual: proof_version_felt });
        }
        ProofVersion::V1 => {
            let proof_output = privacy_circuit_verify_v1::PrivacyProofOutput {
                proof: proof_bytes,
                output_preimage,
            };
            privacy_circuit_verify_v1::verify_recursive_circuit(&proof_output)
                .map_err(|e| VerifyProofError::Verification(e.to_string()))?;
        }
    }

    Ok(())
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L279-318)
```text
    local proof_facts_size;
    local proof_facts: felt*;
    %{ TxProofFacts %}

    let poseidon_ptr = builtin_ptrs.selectable.poseidon;
    with poseidon_ptr {
        let transaction_hash = compute_invoke_transaction_hash(
            common_fields=common_tx_fields,
            execution_context=tx_execution_context,
            account_deployment_data_size=account_deployment_data_size,
            account_deployment_data=account_deployment_data,
            proof_facts_size=proof_facts_size,
            proof_facts=proof_facts,
        );
    }
    update_poseidon_in_builtin_ptrs(poseidon_ptr=poseidon_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    fill_account_tx_info(
        transaction_hash=transaction_hash,
        common_tx_fields=common_tx_fields,
        account_deployment_data_size=account_deployment_data_size,
        account_deployment_data=account_deployment_data,
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        tx_info_dst=tx_info,
        deprecated_tx_info_dst=tx_execution_context.deprecated_tx_info,
    );

    check_and_increment_nonce(tx_info=tx_info);

    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L395-424)
```rust
    /// Runs proof verification: checks if the proof already exists, and if not, verifies it.
    /// Returns `true` if verification was performed, `false` if skipped (proof already stored).
    /// This is the shared verification logic used by both gateway and consensus flows.
    async fn run_proof_verification(
        proof_facts: ProofFacts,
        proof: Proof,
        proof_manager_client: SharedProofManagerClient,
    ) -> Result<bool, TransactionConverterError> {
        let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;

        if contains_proof {
            return Ok(false);
        }

        let proof_facts_hash = proof_facts.hash();
        let verify_start = Instant::now();
        tokio::task::spawn_blocking(move || {
            starknet_proof_verifier::verify_proof(proof_facts, proof)
        })
        .await
        .expect("proof verification task panicked")?;
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
    }
```
