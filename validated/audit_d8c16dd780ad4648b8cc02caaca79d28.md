## Finding: Proof-fact verification cache is keyed only by content hash, not bound to the submitting transaction/sender

### Title
Client-side proving cache accepts previously-verified `proof_facts` for any unrelated invoke transaction - ([File: crates/apollo_transaction_converter/src/transaction_converter.rs])

### Summary
The sequencer's client-side proving pipeline mirrors the Logto bug class: a "verification record" (here, a cached, previously-verified `(proof_facts, proof)` pair) is treated as generically valid once `is_verified`-equivalent (`contains_proof == true`), without checking that it is bound/scoped to the specific transaction (sender, calldata, nonce) currently being admitted.

### Finding Description
When a V3 Invoke transaction carries non-empty `proof_facts`/`proof`, the gateway calls `run_proof_verification`, which first checks the proof manager cache keyed solely by `proof_facts.hash()`: [1](#0-0) 

If any transaction has ever stored a proof for that same `proof_facts` value, verification is skipped entirely (`return Ok(false)`), regardless of which account or calldata now presents that `proof_facts`.

The stateful check that runs later in `AccountTransaction::validate_proof_facts` (blockifier) only validates the *content* of the proof facts — proof version, allowed program hash, block hash/number consistency with stored history, and config hash — none of which depend on `sender_address`, `calldata`, or `nonce` of the invoke transaction carrying them: [2](#0-1) 

The `ProofFactsVariant` parsed from the felt array likewise only extracts `program_hash`, `output_version`, `block_number`, `block_hash`, `config_hash` — nothing that ties the facts to a specific sender or calldata: [3](#0-2) 

The virtual-OS output format documentation itself confirms `proof_facts` only commit to block info and L2-to-L1 message hashes produced by *some* execution, with no claim of ownership tied to a particular sender/calldata pair outside of the invoke transaction hash itself: [4](#0-3) 

Because the transaction hash computation includes `proof_facts` as raw bytes (not a derived commitment to the wrapping tx's sender/calldata), an attacker can take a `(proof_facts, proof)` pair that was legitimately verified once for transaction A and attach the identical bytes to a new, unrelated transaction B (different sender, calldata, nonce, signature). At the gateway, `contains_proof(proof_facts)` returns true (same hash), so the expensive cryptographic `starknet_proof_verifier::verify_proof` check is skipped, and stateful `validate_proof_facts` passes because it never checks sender/calldata binding.

### Impact Explanation
This is exactly the "verification-record scope confusion" pattern from the Logto CVE: a purpose-bound verification (a proof of *a specific* virtual-OS execution) is accepted as a generic "already verified" flag and reused for a different, unrelated transaction/sender because the cache key and the stateful check omit binding to the requesting party. Any sequencer feature that trusts `proof_facts` being present/valid as an indicator that "this transaction's execution was proven off-chain" can be spoofed by attaching stale, unrelated proof data, letting an unprivileged transaction sender bypass the intended client-side-proving guarantee for a transaction whose real content was never proven.

### Likelihood Explanation
Reachable by any transaction sender who can submit an Invoke V3 transaction with `proof_facts`/`proof` fields (gated only by `allow_client_side_proving` config, not by any additional trust). Obtaining a previously verified `proof_facts` value is straightforward, since these are public transaction fields visible on submitted transactions or in blocks.

### Recommendation
Bind `proof_facts` (or the verification cache key) to the specific transaction's identity — e.g., include `sender_address`, `calldata` hash, and `nonce` in the proof-manager cache key, or require `validate_proof_facts` to check that the proof facts commit to the invoking transaction's own execution content, not merely block/global constants.

### Proof of Concept
Conceptual: Submit transaction A with valid client-side-proven `proof_facts`/`proof`; wait for it to be gateway-verified and cached. Submit transaction B (different sender/calldata/nonce/signature) that copies the exact same `proof_facts` and `proof` bytes from A. Gateway's `contains_proof` check returns true and skips cryptographic verification; blockifier's `validate_proof_facts` passes because its checks are independent of sender/calldata. Transaction B is admitted despite its `proof_facts` describing an unrelated execution.

**Caveat**: I could not fully confirm from the indexed code exactly what downstream state-transition or fee benefit is functionally unlocked by "valid" `proof_facts` beyond passing this specific check (e.g., whether it also affects fee charging, `__validate__` skipping, or L2-to-L1 message crediting) — this would need further investigation via a Devin session with full repository access to trace all consumers of `proof_facts`/`ProofFactsVariant` in execution and fee logic, since index size limits may have excluded some file contents.

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-415)
```rust
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

**File:** crates/starknet_api/src/transaction/fields.rs (L759-792)
```rust
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/virtual_os_output.cairo (L14-24)
```text
// 1. Output format:
//    The output is a flat array of felts with the following layout:
//      [output_version, base_block_number, base_block_hash, starknet_os_config_hash,
//       n_l2_to_l1_messages, message_hash_0, message_hash_1, ...]
//    - output_version: the VIRTUAL_OS_OUTPUT_VERSION constant.
//    - base_block_number / base_block_hash: the block this run is based on. The hash is
//      computed (proven) by the OS from the block info and the initial state root.
//    - starknet_os_config_hash: Poseidon hash of the Starknet OS config.
//    - n_l2_to_l1_messages: count of L2-to-L1 message hashes that follow.
//    - Each message hash is Poseidon([from_address, to_address, payload_size, ...payload]).
//    No state diff, data availability, or state roots are included.
```
