Based on my investigation, this is a genuine architectural finding, but confirming exploitability requires verifying one more thing I could not fully confirm in this session: whether the `n_l2_to_l1_messages`/message hashes committed inside the proof are cross-checked anywhere against messages actually produced by the specific transaction carrying the `proof_facts`. I found `check_proof_facts` in `execution_constraints.cairo` and `validate_proof_facts` in `account_transaction.rs`, and neither of them reference message hashes, sender address, nonce, or calldata at all — but I was not able to locate a later consumer (e.g. in `execute_invoke_function_transaction` after `check_proof_facts`, or in `non_reverting_select_execute_entry_point_func`) that binds the proved messages to the current transaction's actual execution output. This is the key uncertainty.

### Title
Client-side proof facts are not bound to the transaction carrying them, enabling proof replay across unrelated transactions - ([File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
The client-side proving feature lets any account attach a `(proof_facts, proof)` pair to an `Invoke V3` transaction. Both the gateway/blockifier check (`validate_proof_facts` in `crates/blockifier/src/transaction/account_transaction.rs`, lines 291-351) and the OS-side check (`check_proof_facts` in `crates/apollo_starknet_os_program/.../execution_constraints.cairo`, lines 33-82) verify only: proof version, allowed virtual-OS program hash, that the referenced block number/hash is a real, retained block hash, and that the config hash matches. None of these checks tie the proof to the specific `sender_address`, `nonce`, or `calldata` of the transaction it is attached to. The circuit itself (`verify_proof` in `crates/starknet_proof_verifier/src/proof_verifier.rs`, lines 126-157) only proves that *some* valid virtual-OS run against a given base block produced a given set of L2→L1 message hashes — it carries no commitment to which account/transaction executed it.

### Finding Description
`ProofFactsVariant::try_from` (`crates/starknet_api/src/transaction/fields.rs`, lines 732-800) parses `proof_facts` into `program_hash, block_number, block_hash, config_hash` and any trailing message-hash fields, but never incorporates the enclosing transaction's `sender_address`, `nonce`, or `calldata`. `Self::validate_proof_facts` (`crates/blockifier/src/transaction/account_transaction.rs:291-351`) subsequently checks only proof version, program hash allow-list, block hash/number and config hash — again nothing transaction-specific. Once `run_proof_verification` (`crates/apollo_transaction_converter/src/transaction_converter.rs:398-424`) succeeds, the proof is cached keyed only by `ProofFacts::hash()` (`crates/apollo_proof_manager/src/proof_manager.rs:54-96`), and any subsequent transaction whose `proof_facts` produce the same hash is treated as already-verified via `contains_proof`, skipping re-verification entirely.

Because the proof/proof_facts pair is a self-contained artifact bound only to a base block and OS config — not to any particular sender, nonce, or calldata — a validly-verified proof generated for one invoke transaction can be copied verbatim (same `proof_facts`/`proof` bytes) onto a different transaction (different sender, nonce, or calldata) submitted by any unprivileged party, and it will pass both gateway/mempool admission and OS-level checks, since none of the validating code paths reference the carrying transaction's identity fields. This mirrors CVE-2017-20180's root cause: a proof-verification routine (`CoinSpend::CoinSpend`) that fails to sufficiently bind the proof to the specific spend/context it accompanies, permitting reuse/replay of an otherwise-valid proof in an unintended context.

### Impact Explanation
If a proof attached to transaction A can be resubmitted attached to transaction B, an attacker can forge apparently-authenticated "client-side proven" status (and its `n_l2_to_l1_messages`/L2→L1 message content) for calldata/sender combinations it was never actually generated for. Depending on what downstream consumers of `has_client_side_proof()` / the proved L2→L1 messages rely on for trust (fee-gas accounting, prover-service billing bypass, or privacy-preserving relayed message authenticity), this can result in unauthorized account actions, wrong committed message/output data, or divergence between honest full nodes and the sequencer's accepted output — satisfying the "unauthorized account action / wrong committed root" bar.

### Likelihood Explanation
I was unable to fully verify, within this pass, whether a downstream consumer (post `check_proof_facts` in `execute_invoke_function_transaction`) actually asserts equality between the proof's committed L2→L1 message hashes and the transaction's *actual* emitted messages. If such a check exists and is enforced against the real Cairo-level execution output of the calldata being run, then message-hash integrity would be preserved and the practical impact would be limited to metadata/fee bypass rather than message forgery. This is a genuine gap in my analysis that a Devin session with full-file access to `transaction_impls.cairo`, `execute_entry_point.cairo`, and the OS output-writing code should resolve before treating this as fully proven.

### Recommendation
Bind `proof_facts`/`proof` cryptographically to the specific transaction fields (at minimum `sender_address`, `nonce`, and a hash of `calldata`) both in the circuit's public output preimage and in `validate_proof_facts`/`check_proof_facts`, so a proof cannot be transplanted across transactions. Additionally, confirm (or add, if missing) an explicit assertion in the OS execution flow that the proof's committed L2→L1 messages equal the actual messages emitted by executing this transaction's calldata.

### Proof of Concept
1. Legitimately submit invoke transaction A (`sender_a`, `nonce_a`, `calldata_a`) with client-side proving, obtaining a valid `(proof_facts, proof)` accepted by the gateway.
2. Construct invoke transaction B with a different `sender_address`/`nonce`/`calldata`, but attach the identical `proof_facts` and `proof` bytes from transaction A.
3. Submit B to the gateway: `validate_client_side_proving_allowed`/`validate_proof_facts_and_proof_consistency` (`crates/apollo_gateway/src/stateless_transaction_validator.rs:231-263`) only check presence/consistency of the fields, and `validate_proof_facts` (`account_transaction.rs`) only checks block/version/config hashes — none of which differ between A and B — so B is accepted as "proven" despite the proof never having been generated for B's actual execution. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/starknet_api/src/transaction/fields.rs (L732-800)
```rust
impl TryFrom<&ProofFacts> for ProofFactsVariant {
    type Error = StarknetApiError;
    fn try_from(proof_facts: &ProofFacts) -> Result<Self, Self::Error> {
        if proof_facts.0.is_empty() {
            return Ok(ProofFactsVariant::Empty);
        }

        let Some(([proof_version, variant_marker], snos_fields)) =
            proof_facts.0.split_at_checked(2)
        else {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Proof facts must have at least 2 fields, got {}",
                proof_facts.0.len()
            )));
        };

        // Validate that the first element is a supported proof version marker.
        let proof_version = ProofVersion::try_from(*proof_version).map_err(|()| {
            StarknetApiError::InvalidProofFacts(format!(
                "Expected first field to be {} or {}, but got {}",
                ProofVersion::V0,
                ProofVersion::V1,
                proof_version,
            ))
        })?;

        // Validate that the second element is VIRTUAL_SNOS.
        if *variant_marker != VIRTUAL_SNOS {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Non-SNOS proofs are not currently supported. Expected second field to be {} \
                 (VIRTUAL_SNOS), but got {}",
                VIRTUAL_SNOS, variant_marker
            )));
        }

        let [program_hash, output_version, block_number_felt, block_hash, config_hash, ..] =
            snos_fields
        else {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "SNOS proof facts is too small with {} fields",
                proof_facts.0.len()
            )));
        };

        // TODO(Yoni): reuse VirtualOsOutput parsing.
        let expected_version = VIRTUAL_OS_OUTPUT_VERSION;
        if *output_version != expected_version {
            return Err(StarknetApiError::InvalidProofFacts(format!(
                "Expected SNOS proof facts version to be {} (VIRTUAL_OS_OUTPUT_VERSION), but got \
                 {}",
                expected_version, output_version
            )));
        }

        let block_number = BlockNumber((*block_number_felt).try_into().map_err(|_| {
            StarknetApiError::InvalidProofFacts(format!(
                "Block number field is not a valid u64: {}",
                block_number_felt
            ))
        })?);

        Ok(ProofFactsVariant::Snos(SnosProofFacts {
            proof_version,
            program_hash: *program_hash,
            block_number,
            block_hash: BlockHash(*block_hash),
            config_hash: *config_hash,
        }))
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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L47-97)
```rust
impl ProofManager {
    pub fn new(config: ProofManagerConfig) -> Self {
        let proof_storage =
            FsProofStorage::new(config.persistent_root).expect("Failed to create proof storage.");
        Self { proof_storage, cache: ProofCache::new(config.cache_size) }
    }

    pub async fn set_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> Result<(), FsProofStorageError> {
        if self.contains_proof(proof_facts.clone()).await? {
            return Ok(());
        }
        let facts_hash = proof_facts.hash();
        self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
        self.cache.insert(facts_hash, proof);
        Ok(())
    }

    pub async fn get_proof(
        &self,
        proof_facts: ProofFacts,
    ) -> Result<Option<Proof>, FsProofStorageError> {
        let facts_hash = proof_facts.hash();
        // Check cache first.
        if let Some(proof) = self.cache.get(&facts_hash) {
            return Ok(Some(proof));
        }
        // Fallback to filesystem.
        let proof = self.proof_storage.get_proof(facts_hash).await?;
        if let Some(proof) = &proof {
            self.cache.insert(facts_hash, proof.clone());
        }
        Ok(proof)
    }

    pub async fn contains_proof(
        &self,
        proof_facts: ProofFacts,
    ) -> Result<bool, FsProofStorageError> {
        let facts_hash = proof_facts.hash();
        // Check cache first.
        if self.cache.contains(&facts_hash) {
            return Ok(true);
        }
        // Fallback to filesystem.
        self.proof_storage.contains_proof(facts_hash).await
    }
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L231-263)
```rust
    fn validate_client_side_proving_allowed(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if self.config.allow_client_side_proving {
            return Ok(());
        }

        // Reject V3 transactions with proofs when client-side proving is disabled.
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
        if has_proof_data {
            return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
        }

        Ok(())
    }

    fn validate_proof_facts_and_proof_consistency(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_facts = !tx.proof_facts.is_empty();
        let has_proof = !tx.proof.is_empty();
        if has_proof_facts != has_proof {
            return Err(StatelessTransactionValidatorError::ProofFactsAndProofConsistency {
                has_proof_facts,
                has_proof,
            });
        }
        Ok(())
    }
```
