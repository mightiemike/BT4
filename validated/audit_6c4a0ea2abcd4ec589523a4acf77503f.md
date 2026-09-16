### Title
Proof verification bypass via `contains_proof` cache reuse — cross-transaction proof-facts replay skips cryptographic proof verification - ([File: crates/apollo_transaction_converter/src/transaction_converter.rs])

### Summary
Client-side proving lets an `Invoke` V3 sender attach `proof_facts` + `proof` fields that the sequencer is supposed to cryptographically verify before trusting them during account execution. The verification, however, is short-circuited by a "already verified" cache check keyed solely on the `proof_facts` value, not on the submitting transaction/sender. Any unprivileged sender can copy previously-verified `proof_facts` bytes from an unrelated public transaction into their own transaction (with an arbitrary/garbage `proof`) and have the sequencer treat them as verified without ever re-running `verify_proof`.

### Finding Description
`StatelessTransactionValidator::validate_proof_facts_and_proof_consistency` only checks that `proof_facts` and `proof` are both empty or both non-empty — it never binds them to the sender, nonce, or calldata of the transaction carrying them [1](#0-0) .

The actual cryptographic check happens later, inside `TransactionConverter::run_proof_verification`, which is used by both the gateway ingestion path and the consensus/batcher path: [2](#0-1) 

This function first calls `proof_manager_client.contains_proof(proof_facts.clone())`; if the *exact same `proof_facts` value* was ever verified and stored before (by any past transaction from any sender), the function returns `Ok(false)` and **`starknet_proof_verifier::verify_proof` is never invoked** — the accompanying `proof` bytes on the current transaction are not checked at all. The lookup key is `proof_facts` alone (via `proof_facts.hash()`), with no cryptographic or logical binding to the transaction's sender address, nonce, or calldata that consumes those facts [3](#0-2) .

Both call sites rely on this same "verify-once, trust-forever-by-value" cache:
- Gateway flow: `spawn_proof_verification` → `run_proof_verification`, then the gateway stores the proof and forwards the transaction to the mempool once verification "succeeds" (or is skipped) [4](#0-3) .
- Consensus/batcher flow: `spawn_verify_and_store_proof` performs the same skip-if-cached logic [5](#0-4) .

This is directly analogous to the Portainer CVE pattern: a security-critical restriction (here, "this `proof_facts` payload was cryptographically proven correct") is enforced once at a single ingestion point and then cached/trusted by value for all future, unrelated consumers, instead of being re-derived or bound to the specific requester/context each time it is used. Just as Portainer trusted a client-supplied flag once at the UI layer and never re-checked it server-side per request, the sequencer trusts a `proof_facts` blob once (for the first submitter) and never re-verifies the cryptographic linkage for any subsequent, unrelated transaction that merely repeats the same bytes.

### Impact Explanation
The `proof_facts` are designed to let account contracts consume externally-verified facts via `get_tx_info().proof_facts` in `__validate__`/`__execute__` without the sequencer re-deriving them on-chain (this is the entire point of the "client-side proving" feature). Because the verification cache is keyed only by the `proof_facts` value and not bound to the submitting sender/nonce/calldata, an unprivileged attacker can:
1. Observe any public `Invoke` V3 transaction with non-empty `proof_facts` (these are visible transaction fields, and per `store_proof_and_spawn_archiving`/`store_proof_in_proof_manager` they get persisted for reuse) [6](#0-5) .
2. Copy the exact `proof_facts` bytes into their own, unrelated transaction (different sender, nonce, calldata, entry point) together with an arbitrary/garbage `proof` value that satisfies only the non-emptiness check.
3. Have the gateway/mempool/batcher accept the transaction as "proof-verified" purely because `contains_proof` already returns true, with no cryptographic proof ever validated for this specific transaction.

Any account contract logic that trusts `proof_facts` from `tx_info` to authorize account actions (e.g., gating a spend, claim, or privileged operation on an externally-proven fact) can be tricked into acting on facts that were never proven for that account/context, leading to unauthorized account action or loss of funds.

### Likelihood Explanation
High. No special privileges are required — an unprivileged sender only needs to observe one public transaction using client-side proving and resubmit its `proof_facts` verbatim in their own transaction. The bypass requires no cryptographic break; it exploits the value-keyed cache design directly (`contains_proof` short-circuit) rather than any weakness in `verify_proof` itself.

### Recommendation
Bind proof verification to the specific transaction context rather than caching purely by `proof_facts` value:
- Include sender address, nonce, and/or transaction hash as part of the cache key checked by `contains_proof`, or
- Always re-run `verify_proof` for every transaction regardless of cache state, treating the proof-manager cache purely as a storage/dedup optimization for the underlying facts data rather than as an authorization bypass, or
- Require the `proof` to cryptographically attest to a binding that includes the specific transaction's identity (e.g., sign over sender/nonce in addition to the facts), so a stolen `proof_facts` blob cannot be reused across unrelated transactions.

### Proof of Concept
1. Attacker A submits a legitimate `Invoke` V3 transaction with valid `proof_facts = F` and matching `proof = P`; the gateway calls `run_proof_verification`, `contains_proof(F)` is false, `verify_proof(F, P)` succeeds, and `F` is stored in the proof manager [7](#0-6) .
2. Attacker B (unrelated sender, different account/nonce/calldata) submits their own `Invoke` V3 transaction reusing `proof_facts = F` but with `proof = garbage` (any non-empty bytes, satisfying `validate_proof_facts_and_proof_consistency`'s only check that presence of `proof_facts` matches presence of `proof`) [1](#0-0) .
3. In `run_proof_verification`, `contains_proof(F)` returns `true`, so `verify_proof` is skipped entirely for Attacker B's transaction; the transaction proceeds through gateway/mempool/batcher as if its (invalid) `proof` had been cryptographically validated [8](#0-7) .
4. If Attacker B's account contract's `__validate__`/`__execute__` reads `proof_facts` from `tx_info` and grants an action based on the (falsely trusted) facts `F`, Attacker B obtains an unauthorized account action/asset transfer without ever having supplied a valid proof for their own transaction.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L249-263)
```rust
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L443-471)
```rust
    /// Spawns a single task that verifies the proof and then stores it in the proof manager.
    /// Used by the consensus flow, where tasks run concurrently with batcher execution and
    /// are awaited at fin.
    fn spawn_verify_and_store_proof(
        &self,
        proof_facts: ProofFacts,
        proof: Proof,
    ) -> VerifyAndStoreProofTask {
        let pmc = self.proof_manager_client.clone();
        let proof_facts_hash = proof_facts.hash();
        tokio::spawn(async move {
            let verified =
                Self::run_proof_verification(proof_facts.clone(), proof.clone(), pmc.clone())
                    .await?;

            if !verified {
                return Ok(());
            }

            let start = Instant::now();
            pmc.set_proof(proof_facts, proof).await?;
            let duration = start.elapsed();
            CONSENSUS_PROOF_MANAGER_STORE_LATENCY.record(duration.as_secs_f64());
            info!(
                "Proof manager store took: {duration:?} for proof facts hash: {proof_facts_hash:?}"
            );
            Ok(())
        })
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L300-336)
```rust
    async fn store_proof_and_spawn_archiving(
        &self,
        proof_data: Option<(ProofFacts, Proof)>,
        tx_hash: TransactionHash,
        is_p2p: bool,
    ) -> GatewayResult<ProofArchiveHandle> {
        let Some((proof_facts, proof)) = proof_data else {
            return Ok(None);
        };

        // Spawn the GCS archive write before the proof-manager store so the two run in parallel
        // — the proof-manager store is the dominant latency and there's no point serializing them.
        let archive_handle = if is_p2p {
            // Skip the GCS archive write for transactions received via P2P to avoid double writes.
            None
        } else {
            let proof_archive_writer = self.proof_archive_writer.clone();
            let archive_proof_facts = proof_facts.clone();
            let archive_proof = proof.clone();
            Some(tokio::spawn(async move {
                let proof_facts_hash = archive_proof_facts.hash();
                let proof_archive_writer_start = Instant::now();
                let result =
                    proof_archive_writer.set_proof(archive_proof_facts, archive_proof).await;
                let proof_archive_writer_duration = proof_archive_writer_start.elapsed();
                info!(
                    "Proof archive writer took: {proof_archive_writer_duration:?} for tx hash: \
                     {tx_hash:?}"
                );
                (proof_facts_hash, result)
            }))
        };

        // Proof is verified during conversion to internal tx. It is stored here, after
        // validation, to avoid storing proofs for rejected transactions.
        let store_result =
            self.transaction_converter.store_proof_in_proof_manager(proof_facts, proof).await;
```
