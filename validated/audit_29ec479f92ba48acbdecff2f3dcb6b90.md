Based on my investigation, I found a strong analog to the Zebra CVE's bug class: a verification cache keyed by a partial/non-binding identifier that allows skipping cryptographic verification for data that hasn't actually been checked.

### Title
Proof-Verification Bypass via `ProofFacts`-Hash Cache Collision Skips STARK Proof Check for Client-Side-Proving Invoke Transactions - (File: `crates/apollo_transaction_converter/src/transaction_converter.rs`)

### Summary
The proof-manager cache used to avoid redundant proof verification is keyed solely by `proof_facts.hash()` — the hash of the *public claim* (block info, config hash, L2-to-L1 message hashes) — and never by the actual `Proof` bytes submitted with a transaction. `run_proof_verification` treats `contains_proof(proof_facts) == true` as sufficient evidence that "this proof was already verified," and unconditionally skips calling `starknet_proof_verifier::verify_proof` for any later transaction that reuses the same `proof_facts` value, regardless of what (possibly invalid/garbage) `proof` bytes it supplies.

### Finding Description
`ProofFacts` is the public output committed to by the "virtual OS" STARK proof for client-side-proving Invoke V3 transactions: `[proof_version, variant, program_hash, output_version, base_block_number, base_block_hash, starknet_os_config_hash, n_l2_to_l1_messages, message_hash_0, ...]` [1](#0-0) . Crucially, this array does **not** include the sender address, calldata, or nonce of the invoke transaction that ultimately submits it — it only commits to the base block and the L2-to-L1 messages produced by *some* virtual OS run.

The cache/storage layer keys everything by `proof_facts.hash()`, ignoring the actual `Proof` bytes: [2](#0-1) [3](#0-2) 

The transaction converter's verification entry point trusts this cache blindly: [4](#0-3) 

Both the gateway ingestion path (`spawn_proof_verification`) and the consensus-tx conversion path (`spawn_verify_and_store_proof`) call `run_proof_verification` and treat a `contains_proof` hit as "already verified," never invoking `verify_proof` on the newly submitted bytes: [5](#0-4) 

The filesystem backing store even documents (and relies on) an assumption that this is safe because "proofs are deterministic for a given facts_hash" — but that assumption only holds for the *storage* collision (two concurrent honest writers), not for an attacker deliberately supplying different (invalid) bytes for a `proof` under a `facts_hash` that some other, unrelated transaction already caused to be cached as verified: [6](#0-5) 

**Attack path (single unprivileged transaction sender):**
1. Any user submits (or the attacker observes on the network) a legitimate client-side-proving Invoke V3 transaction `tx1` with `proof_facts = F` and a valid `proof = P1`. The gateway verifies `P1` against `F` and, via `store_proof_and_spawn_archiving` → `store_proof_in_proof_manager`, persists `(F, P1)` in the `ProofManager`/`ProofCache`, keyed only by `hash(F)` [7](#0-6) .
2. The attacker crafts their own Invoke V3 transaction `tx2` (their own sender address, nonce, calldata — completely unrelated to `tx1`) but copies the exact same `proof_facts = F` bytes (which are public, taken verbatim from `tx1`), and attaches an arbitrary/garbage `proof = P2` (e.g., empty or malformed bytes).
3. When `tx2` is converted (`convert_rpc_tx_to_internal_rpc_tx` → `spawn_proof_verification` → `run_proof_verification`), `contains_proof(F)` returns `true` (because `F`'s hash was cached from `tx1`), so `verify_proof` is **never called** on `P2`, and verification is reported as trivially successful.
4. `tx2` proceeds through the gateway/mempool/batcher with an unverified, attacker-controlled `proof` value, and its resource-bound/fee accounting for `has_client_side_proof` is honored (extra L2 gas reservation is applied and accepted) as if a genuine STARK proof had backed it.

Since `check_proof_facts` in the OS/blockifier pre-validation only checks block-hash/config-hash/version fields of `F` — not that the specific `proof` cryptographically ties to `tx2` — and since the reused `F` is not bound to `tx2`'s sender/calldata at all, the cache-based skip lets an attacker completely avoid producing (or possessing) any valid STARK proof for their own transaction while it is treated as a proven client-side-proving transaction.

### Impact Explanation
This breaks the core security guarantee of the client-side-proving feature: that `proof` cryptographically attests to the claimed `proof_facts` (in particular the committed L2-to-L1 message hashes and base-block binding) for the specific transaction carrying them. An attacker can submit transactions with a completely bogus/unverified `proof` blob as long as they reuse `proof_facts` bytes seen from any prior transaction, causing the sequencer to accept a transaction as "proven" when it is not. Because the proof-skip decision is made identically by every node running this code (the vulnerable logic is deterministic and not proposer-specific), this is not a "malicious proposer" issue — it's a protocol-verification defect reachable by any ordinary transaction sender, meeting the "unauthorized action / verification-bypass" bar analogous to the reported Zebra CVE (skip of an integrity check keyed by a non-binding identifier).

### Likelihood Explanation
High likelihood: the `proof_facts` used for the collision are public (visible on any p2p-propagated or included transaction), require no privileged access, and the attack requires only crafting a normal RPC Invoke V3 transaction with copied `proof_facts` bytes and arbitrary garbage `proof` bytes — well within reach of any unprivileged transaction sender via the gateway's public RPC endpoint.

### Recommendation
Key the proof cache/storage by a hash that binds both `proof_facts` **and** the actual `proof` bytes (e.g., `hash(proof_facts || proof)`), or always re-verify `proof` against `proof_facts` when a transaction submits proof data and cache only the fact "this exact (facts, proof) pair verified," never treat "this `facts_hash` was seen before" as sufficient to skip verification of a different `proof` payload. Additionally, consider binding `proof_facts` to transaction-specific data (e.g., include a commitment to sender/calldata within the SNOS output) so `proof_facts` reuse across unrelated transactions is inherently detected/rejected even before the proof-cache optimization is applied.

### Proof of Concept
Not directly executable without live infra, but reproducible logically from the code:
1. Submit `tx1`: Invoke V3, `proof_facts = F`, valid `proof = P1` → gateway verifies and stores `(F, P1)` (`ProofManager::set_proof`), tx included in a block.
2. Submit `tx2`: Invoke V3 from a different sender/nonce/calldata, `proof_facts = F` (byte-identical to `tx1`), `proof = P2 = Proof::from(vec![])` or any random bytes.
3. Trace `convert_rpc_tx_to_internal_rpc_tx(tx2)` → `spawn_proof_verification(F, P2)` → `run_proof_verification(F, P2, pmc)`: `pmc.contains_proof(F)` returns `Ok(true)` (cached from step 1) → function returns `Ok(false)` immediately, `starknet_proof_verifier::verify_proof` is never invoked on `P2` [8](#0-7) .
4. `tx2` is accepted as if its (bogus) proof were cryptographically verified.

### Citations

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

**File:** crates/apollo_proof_manager/src/proof_manager.rs (L85-96)
```rust
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
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L398-407)
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
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L446-471)
```rust
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

**File:** crates/apollo_proof_manager/src/proof_storage.rs (L115-138)
```rust
    async fn write_proof_atomically(
        &self,
        facts_hash: Felt,
        proof: Proof,
    ) -> FsProofStorageResult<()> {
        // Write proof to a temporary directory.
        let (_tmp_root, tmp_dir) = self.create_tmp_dir(facts_hash).await?;
        self.write_proof_to_file(&tmp_dir, &proof).await?;

        // Atomically rename directory to persistent one.
        // If a concurrent write already placed the proof at the persistent path, the rename
        // will fail (e.g. ENOTEMPTY on Linux). Since proofs are deterministic for a given
        // facts_hash, the existing proof is identical and we can safely treat this as success.
        let persistent_dir = self.get_persistent_dir_with_create(facts_hash).await?;
        match tokio::fs::rename(&tmp_dir, &persistent_dir).await {
            Ok(()) => Ok(()),
            Err(_)
                if tokio::fs::try_exists(persistent_dir.join("proof")).await.unwrap_or(false) =>
            {
                Ok(())
            }
            Err(e) => Err(e.into()),
        }
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L332-336)
```rust

        // Proof is verified during conversion to internal tx. It is stored here, after
        // validation, to avoid storing proofs for rejected transactions.
        let store_result =
            self.transaction_converter.store_proof_in_proof_manager(proof_facts, proof).await;
```
