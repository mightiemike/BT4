### Title
Proof Verification Bypass via Pre-Populated Proof Manager Ambient State — (`crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary

`run_proof_verification` skips cryptographic proof verification when `contains_proof` returns `true` for the given `proof_facts`. Because `proof_facts` are block-level (not transaction-specific), an adversary who submits one legitimate transaction pre-populates the proof manager's ambient state, causing all subsequent transactions sharing those same `proof_facts` to bypass proof verification entirely. The gateway then accepts these transactions as if they carried valid SNOS proofs.

### Finding Description

`run_proof_verification` in `crates/apollo_transaction_converter/src/transaction_converter.rs` checks ambient state (the proof manager's storage) rather than verifying the actual proof submitted with the incoming transaction:

```rust
async fn run_proof_verification(
    proof_facts: ProofFacts,
    proof: Proof,
    proof_manager_client: SharedProofManagerClient,
) -> Result<bool, TransactionConverterError> {
    let contains_proof = proof_manager_client.contains_proof(proof_facts.clone()).await?;
    if contains_proof {
        return Ok(false);   // ← verification skipped entirely
    }
    // ... actual cryptographic verification only reached when proof is absent
    Ok(true)
}
``` [1](#0-0) 

The proof manager stores proofs keyed by `proof_facts.hash()` — a Poseidon hash of block-level fields (`block_number`, `block_hash`, `program_hash`, `config_hash`):

```rust
pub fn hash(&self) -> Felt {
    HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
}
``` [2](#0-1) 

Because `proof_facts` are block-level, many distinct transactions can legitimately share identical `proof_facts`. The proof manager stores and retrieves proofs keyed only by `proof_facts.hash()`, with no binding to the specific transaction or the specific proof bytes submitted:

```rust
pub async fn set_proof(&self, proof_facts: ProofFacts, proof: Proof) -> ... {
    if self.contains_proof(proof_facts.clone()).await? { return Ok(()); }
    let facts_hash = proof_facts.hash();
    self.proof_storage.set_proof(facts_hash, proof.clone()).await?;
    ...
}
``` [3](#0-2) 

**Attack flow:**

1. Adversary submits **Transaction A** with `proof_facts = P` and a valid `proof = Q1`. `run_proof_verification` finds `contains_proof(P) = false`, verifies Q1 cryptographically, and the gateway stores Q1 keyed by `P.hash()`.

2. Adversary submits **Transaction B** with the **same** `proof_facts = P` but different calldata/sender/nonce and an **invalid** `proof = Q2`.

3. `run_proof_verification(P, Q2, pmc)` calls `contains_proof(P)` → `true` → returns `Ok(false)` without ever calling `verify_proof(P, Q2)`. [4](#0-3) 

4. The spawned verification task returns `Ok(())` (success), so `await_verification_task_and_extract_proof_data` returns `Some((P, Q2))`. [5](#0-4) 

5. `store_proof_and_spawn_archiving

### Citations

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-424)
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
        let verify_duration = verify_start.elapsed();
        PROOF_VERIFICATION_LATENCY.record(verify_duration.as_secs_f64());
        info!(
            "Proof verification took: {verify_duration:?} for proof facts hash: \
             {proof_facts_hash:?}"
        );

        Ok(true)
    }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L709-711)
```rust
    pub fn hash(&self) -> Felt {
        HashChain::new().chain_iter(self.0.iter()).get_poseidon_hash()
    }
```

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L54-66)
```rust
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
```

**File:** crates/apollo_gateway/src/gateway.rs (L467-489)
```rust
    async fn await_verification_task_and_extract_proof_data(
        &self,
        verification_handle: Option<VerificationHandle>,
        tx_signature: &TransactionSignature,
    ) -> Result<Option<(ProofFacts, Proof)>, StarknetError> {
        let Some(handle) = verification_handle else {
            return Ok(None);
        };

        handle
            .verification_task
            .await
            .map_err(|e| {
                warn!("Proof verification task panicked: {}", e);
                StarknetError::internal_with_logging("Proof verification task panicked:", &e)
            })?
            .map_err(|e| {
                warn!("Proof verification failed: {}", e);
                transaction_converter_err_to_deprecated_gw_err(tx_signature, e)
            })?;

        Ok(Some((handle.proof_facts, handle.proof)))
    }
```
