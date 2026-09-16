### Title
Incomplete proof-fact verification allows a client-side proof to authorize an unrelated transaction - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo])

### Summary
`check_proof_facts` in the sequencer OS Cairo program (mirrored by `validate_proof_facts` in the Rust blockifier) only validates the *header* of a submitted `proof_facts` blob — proof version, program hash, output version, staleness/non-triviality of the referenced base block hash, and the virtual-OS config hash. Neither the Cairo function nor its Rust counterpart binds the accompanying cryptographic `proof`/`proof_facts` to the specific invoke transaction that carries it (sender address, calldata, nonce, or fee bounds are never part of what is checked against the proven virtual-OS run).

### Finding Description
`check_proof_facts` [1](#0-0)  validates:
- proof header (`proof_variant`, `program_hash`, `proof_version`)
- `VirtualOsOutputHeader` version
- that `base_block_number` is old enough and `base_block_hash` is non-zero and matches the stored block hash
- that `starknet_os_config_hash` matches

The Rust-side `validate_proof_facts` performs the equivalent checks pre-execution [2](#0-1) , and the cryptographic proof itself is verified separately via `verify_proof`, which only checks that the proof matches the `proof_facts` output preimage — it says nothing about which transaction the facts are permitted to accompany [3](#0-2) .

Critically, none of these three layers (Cairo `check_proof_facts`, Rust `validate_proof_facts`, or `verify_proof`) assert that the proof facts' content is cryptographically tied to *this* transaction's `sender_address`, `calldata`, or `nonce`. The `VirtualOsOutputHeader` only commits to `output_version, base_block_number, base_block_hash, starknet_os_config_hash, n_l2_to_l1_messages` plus L2-to-L1 message hashes [4](#0-3) . The only place proof facts are bound to the transaction is via `compute_invoke_transaction_hash`, which hashes `proof_facts` as an opaque blob into the transaction hash [5](#0-4)  — this only prevents *tampering* with the felts in `proof_facts` after the fact, it does not prove that the *content* of those felts (the virtual-OS-proven message hashes / state effects) actually correspond to the sender, calldata, or nonce of the transaction that references them.

Since `contains_proof`/proof-manager de-duplication is keyed purely by `proof_facts.hash()` [6](#0-5) , and the same `proof_facts` bytes (hence the same accepted proof) can legitimately be copy-pasted verbatim into a different invoke transaction's fields (different sender/calldata/nonce/signature) without failing any of `check_proof_facts`, `validate_proof_facts`, or `verify_proof` — this is structurally analogous to the Aztec Connect incident, where an incomplete proof-verification check failed to bind the proof to the specific action being authorized, letting an attacker replay/reuse a valid proof for an unintended state transition.

### Impact Explanation
If a validated `proof_facts`/`proof` pair (e.g. one legitimately produced for account A's client-side-proven invocation) can be resubmitted unchanged as part of a different transaction (different sender, calldata, or nonce) and pass gateway, mempool, and OS re-execution checks, an attacker could get the sequencer/OS to accept forged authorization state for an unrelated call. Depending on how the L2-to-L1 message hashes embedded in the virtual-OS output are subsequently consumed by contracts trusting `n_l2_to_l1_messages`/message hashes proven off-chain, this could enable unauthorized L1 message emission or unauthorized account actions bound to the wrong sender — a concrete loss/unauthorized-account-action class of impact.

### Likelihood Explanation
Reaching this path requires only submitting an ordinary V3 Invoke transaction with attacker-chosen `proof_facts` and `proof` fields through the gateway — no privileged role is needed. The main blocking factor for full exploitation is unknown application-level use of the `n_l2_to_l1_messages` output by consuming contracts; I was not able to locate, within the indexed code, the specific point where the virtual-OS proven message hashes are consumed against the calling transaction's identity (sender/calldata) to confirm whether a downstream binding check exists elsewhere (e.g., in contract logic outside this repo, or in a hint/syscall implementation not covered by the search). This uncertainty means the finding should be treated as a strong architectural gap in binding rather than a fully proven end-to-end fund-loss exploit.

### Recommendation
Extend `check_proof_facts` (Cairo) and `validate_proof_facts` (Rust) to cryptographically bind the proof facts to the specific transaction: include the invoking transaction's `sender_address` (and ideally `nonce`/relevant calldata commitment) as part of what the virtual OS proves and what `VirtualOsOutputHeader` commits to, and assert that field matches the enclosing transaction's `sender_address` before accepting the proof facts as valid authorization.

### Proof of Concept
Not directly reproducible from the indexed sources: constructing a concrete PoC requires generating a real client-side proof (`privacy_circuit_verify_v1`) and observing how a downstream consumer trusts the `VirtualOsOutputHeader.n_l2_to_l1_messages`/message hashes tied to sender identity, which is outside what was found in this index. Based on the reachable code, the structural PoC sketch is:
1. Legitimately obtain a valid `(proof_facts, proof)` pair for account A's invoke transaction (client-side proving flow, e.g. `run_virtual_os`).
2. Submit a new Invoke V3 transaction with `sender_address = B`, different `calldata`/`nonce`, but the identical `proof_facts`/`proof` bytes from step 1.
3. Observe that `check_proof_facts`/`validate_proof_facts`/`verify_proof` all pass since none check sender/calldata binding, only header/version/block-hash/config-hash and circuit validity of the opaque facts blob [1](#0-0) [2](#0-1) .

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

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L123-157)
```rust
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L58-66)
```text
// The header of the virtual OS output.
struct VirtualOsOutputHeader {
    output_version: felt,
    // The block number and hash that this run is based on.
    base_block_number: felt,
    base_block_hash: felt,
    starknet_os_config_hash: felt,
    n_l2_to_l1_messages: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L183-211)
```text
func compute_invoke_transaction_hash{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    common_fields: CommonTxFields*,
    execution_context: ExecutionContext*,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
    proof_facts_size: felt,
    proof_facts: felt*,
) -> felt {
    alloc_locals;

    // TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
    assert account_deployment_data_size = 0;
    with_attr error_message("Invalid transaction version: {version}.") {
        assert common_fields.version = 3;
    }

    let hash_state: PoseidonHashState = poseidon_hash_init();
    with hash_state {
        hash_tx_common_fields(common_fields=common_fields);
        poseidon_hash_update_with_nested_hash(
            data_ptr=account_deployment_data, data_length=account_deployment_data_size
        );
        poseidon_hash_update_with_nested_hash(
            data_ptr=execution_context.calldata, data_length=execution_context.calldata_size
        );
        // For backward compatibility, we don't hash proof facts if they are empty.
        if (proof_facts_size != 0) {
            poseidon_hash_update_with_nested_hash(
                data_ptr=proof_facts, data_length=proof_facts_size
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
