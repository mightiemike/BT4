### Title
Client-side proving proof facts are not bound to the invoking transaction's calldata/sender/entry-point, allowing proof replay across arbitrary invoke transactions - (File: crates/starknet_api/src/transaction/fields.rs, crates/starknet_proof_verifier/src/proof_verifier.rs)

### Summary
The "client-side proving" feature (`proof_facts` + `proof` on Invoke V3 transactions) verifies only that a virtual-OS run occurred against a specific `(program_hash, block_number, block_hash, config_hash)` tuple. Nothing in the `SnosProofFacts` structure, the cryptographic verification preimage, or the gateway's proof-caching key incorporates the transaction's `sender_address`, `calldata`, `nonce`, or `entry_point_selector`. A proof once verified and cached is trusted for any transaction that reuses the identical `proof_facts` array, regardless of what that new transaction actually calls or who submits it.

### Finding Description
`SnosProofFacts` (the parsed representation of `proof_facts`) only contains `proof_version, program_hash, block_number, block_hash, config_hash`: [1](#0-0) 

The verification preimage fed into the cryptographic circuit (`privacy_circuit_verify_v1::verify_recursive_circuit`) is reconstructed purely from these `proof_facts` felts — it never touches the invoke transaction's `calldata`, `sender_address`, or `entry_point_selector`: [2](#0-1) [3](#0-2) 

The on-chain (Starknet OS) side check is likewise scoped to program hash / block hash / config hash consistency only, never to the specific calldata being executed in the *current* (non-virtual) transaction: [4](#0-3) 

In the gateway/consensus flow, once a proof has been verified for a given `proof_facts` value, it is cached and keyed only by `proof_facts.hash()` (a Poseidon hash over the same five-field tuple). Any subsequent transaction — from any sender, with any calldata — that supplies the same `proof_facts` array skips cryptographic re-verification entirely: [5](#0-4) [6](#0-5) 

Because `proof_facts` (and the values it hashes) are entirely public — visible on every proved transaction on L2 — any unprivileged attacker can copy a previously-published, already-verified `proof_facts` + `proof` pair from any historical transaction and attach it, unmodified, to a brand-new Invoke V3 transaction with completely different `sender_address`, `nonce`, and `calldata`. The gateway's `contains_proof` check will return `true`, verification is skipped (`run_proof_verification` returns `Ok(false)`), and the transaction proceeds to execution as if its client-side-proving claim were legitimately proven — despite the proof never having attested to *this* transaction's actual invocation.

This mirrors the "proofless deposit" root cause in the Hinkal incident: a proof-gated action is accepted because the system checks for proof *existence*/*validity in isolation*, not proof *binding to the specific claim being authorized*.

### Impact Explanation
This is a Medium-severity divergence/inconsistency issue. The concrete effect depends on what downstream logic keys off `proof_facts`/`allow_client_side_proving` (fee waiver, `strict_nonce_check`, or virtual-OS-derived state assumptions) — the codebase in scope confirms fee resource bounds must be zero for virtual-OS proving inputs and that reverted virtual-OS transactions are disallowed, implying proof-gated transactions may receive different fee/execution treatment than ordinary ones. An attacker copying someone else's verified proof onto their own unrelated transaction gains that differentiated treatment without ever having produced a valid proof for their own execution content, which is an unauthorized-action class bug (bypassing a security check meant to bind execution content to a cryptographic attestation).

### Likelihood Explanation
High likelihood of reachability: any unprivileged user can submit an Invoke V3 transaction with `proof_facts`/`proof` copied verbatim from a public block (these fields are printed in RPC responses, e.g. `apollo_starknet_client/resources/reader/block_post_0_14_2.json`), and the gateway performs no per-transaction binding check before treating the (cached) proof as valid.

### Recommendation
Bind the SNOS proof facts cryptographically to the specific transaction being submitted — e.g., include a hash of `sender_address`, `nonce`, `calldata`, and `entry_point_selector` inside the virtual-OS output / proof facts, and validate that binding both in `reconstruct_output_preimage`/`verify_proof` and in the Starknet OS `check_proof_facts` constraint, and use the combined (proof_facts, tx-binding) tuple as the proof-manager cache key rather than `proof_facts.hash()` alone.

### Proof of Concept
1. Observe any historical client-side-proving Invoke V3 transaction on L2 (e.g., the sample in `crates/apollo_http_server/resources/deprecated_gateway/invoke_tx_client_side_proving.json`) and extract its `proof_facts` and `proof` fields.
2. Craft a new Invoke V3 transaction with a different `sender_address`, `nonce`, and `calldata`, but reuse the extracted `proof_facts`/`proof` verbatim.
3. Submit to the gateway. `StatelessTransactionValidator::validate_proof_facts_and_proof_consistency` passes (both present), `TransactionConverter::run_proof_verification` calls `contains_proof(proof_facts)` which returns `true` (already verified/cached from the original transaction), skipping cryptographic re-verification — the new, unrelated transaction is accepted as "proven" without a proof that ever attested to its actual calldata/sender.

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L711-719)
```rust
impl ProofFacts {
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn hash(&self) -> Felt {
        HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
    }
}
```

**File:** crates/starknet_api/src/transaction/fields.rs (L803-813)
```rust
/// Contains the required fields for valid SNOS proof facts.
///
/// A valid SNOS proof facts structure must include these fields as its first five entries.
#[derive(Debug)]
pub struct SnosProofFacts {
    pub proof_version: ProofVersion,
    pub program_hash: StarkHash,
    pub block_number: BlockNumber,
    pub block_hash: BlockHash,
    pub config_hash: StarkHash,
}
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L103-121)
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
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L126-154)
```rust
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L33-81)
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
