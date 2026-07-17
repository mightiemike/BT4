Now I have all the information needed. Let me trace the full attack path precisely.

### Title
Missing Cross-Binding Between `inner` and `metadata` in `ChunkEndorsementV2::verify()` Enables Cache-Poisoning of Validator Endorsements — (`core/primitives/src/stateless_validation/chunk_endorsement.rs`)

---

### Summary

`ChunkEndorsementV2::verify()` independently verifies two separately-signed fields — `inner` (containing `chunk_hash`) and `metadata` (containing `ChunkProductionKey`: `shard_id`, `epoch_id`, `height_created`) — without any cryptographic cross-binding between them. A malicious network peer who observes two valid endorsements from the same validator key for two different chunks can splice their fields together to produce a mixed endorsement that passes `verify()`. When this mixed endorsement is processed first, it poisons the `ChunkEndorsementTracker` cache under `key_B` with `chunk_hash_A`, and the subsequent legitimate endorsement for chunk B from the same validator is silently dropped by the deduplication guard, causing that validator's stake to be excluded from `collect_chunk_endorsements`.

---

### Finding Description

**Root cause — no cross-binding in `verify()`:** [1](#0-0) 

```rust
fn verify(&self, public_key: &PublicKey) -> bool {
    let inner = borsh::to_vec(&self.inner).unwrap();
    let metadata = borsh::to_vec(&self.metadata).unwrap();
    self.signature.verify(&inner, public_key)
        && self.metadata_signature.verify(&metadata, public_key)
}
```

`inner` (signed separately, covering only `chunk_hash` + `"ChunkEndorsement"` differentiator) and `metadata` (signed separately, covering only `account_id`, `shard_id`, `epoch_id`, `height_created`) are each verified against the same public key, but **nothing binds them to each other**. The two signatures are fully independent.

**Construction of the mixed endorsement:**

Given two legitimately observed endorsements from the same validator key `V`:
- `E_A = { inner_A(chunk_hash_A), sig_A, metadata_A(key_A), meta_sig_A }`
- `E_B = { inner_B(chunk_hash_B), sig_B, metadata_B(key_B), meta_sig_B }`

Construct:
- `E_mixed = { inner_A(chunk_hash_A), sig_A, metadata_B(key_B), meta_sig_B }`

`verify(V.pubkey)` on `E_mixed`:
1. `sig_A.verify(borsh(inner_A), V.pubkey)` → **TRUE** (sig_A was produced for inner_A)
2. `meta_sig_B.verify(borsh(metadata_B), V.pubkey)` → **TRUE** (meta_sig_B was produced for metadata_B)

Result: `verify()` returns **TRUE**.

**Cache poisoning via the deduplication guard:** [2](#0-1) 

When `E_mixed` is processed first by `process_chunk_endorsement`:
- `key = endorsement.chunk_production_key()` → `key_B` (from `metadata_B`)
- Deduplication check at line 50 finds no existing entry → proceeds
- `validate_chunk_endorsement` calls `endorsement.verify(validator.public_key())` → TRUE
- Stored: `cache[key_B][V] = (chunk_hash_A, sig_A)`

When the legitimate `E_B` arrives subsequently:
- `key = key_B`, `account_id = V`
- Deduplication check at line 50: `cache.peek(&key_B).is_some_and(|entry| entry.contains_key(V))` → **TRUE**
- Returns early at line 52 — the legitimate endorsement is silently discarded

**Endorsement filtered out at collection time:** [3](#0-2) 

```rust
let validator_signatures = entry
    .into_iter()
    .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
    ...
```

The stored `chunk_hash_A ≠ chunk_hash_B`, so the poisoned entry is filtered out. Validator `V`'s stake contributes zero to the endorsement state for chunk B.

The comment at lines 99–104 explicitly acknowledges this design: "We still need to validate if the chunk_hash matches the chunk_header.chunk_hash()" — but the deduplication guard at line 50 prevents the correct entry from ever replacing the poisoned one.

---

### Impact Explanation

A malicious network peer can suppress one validator's endorsement per targeted chunk per attack. If the chunk's endorsement stake is near the threshold, this can prevent the chunk from being included in a block, causing liveness degradation. The poisoned cache entry persists for the lifetime of the `ChunkProductionKey` in the LRU cache (up to 100 entries).

---

### Likelihood Explanation

The attacker must be a network peer who can observe P2P endorsement messages and relay the mixed endorsement to the chunk producer before the legitimate endorsement arrives. This is a race condition, but one the attacker controls by being a relay node between the validator and the chunk producer. No validator, block producer, or operator privileges are required. The two source endorsements are observable from normal P2P traffic whenever the same validator endorses chunks across two different shards or heights in the same epoch.

---

### Recommendation

Cryptographically bind `inner` and `metadata` together. The simplest fix is to sign a single combined structure instead of two independent structures. For example, sign `(inner, metadata)` as a single Borsh-serialized blob, or include the `chunk_hash` from `inner` inside `metadata` so that `metadata_signature` covers both. A minimal change would be to add a cross-hash: include `hash(borsh(inner))` as a field in `ChunkEndorsementMetadata` before signing it, so that `meta_sig` is invalidated if `inner` is swapped.

---

### Proof of Concept

```rust
// Pseudocode Rust test (production types)
let signer = create_test_validator_signer("validator.near");

// Two chunks with different hashes, same validator
let endorsement_a = ChunkEndorsement::new(epoch_id_a, &chunk_header_a, &signer);
let endorsement_b = ChunkEndorsement::new(epoch_id_b, &chunk_header_b, &signer);

let (inner_a, sig_a) = match &endorsement_a {
    ChunkEndorsement::V2(v2) => (v2.inner.clone(), v2.signature.clone()),
};
let (metadata_b, meta_sig_b) = match &endorsement_b {
    ChunkEndorsement::V2(v2) => (v2.metadata.clone(), v2.metadata_signature.clone()),
};

// Construct mixed endorsement: inner from A, metadata from B
let mixed = ChunkEndorsement::V2(ChunkEndorsementV2 {
    inner: inner_a,
    signature: sig_a,
    metadata: metadata_b,
    metadata_signature: meta_sig_b,
});

// Step 1: verify() returns true despite the mismatch
assert!(mixed.verify(signer.public_key()));

// Step 2: process mixed endorsement first → poisons cache[key_B] with chunk_hash_A
tracker.process_chunk_endorsement(&mixed).unwrap();

// Step 3: process legitimate endorsement_b → silently dropped by dedup guard
tracker.process_chunk_endorsement(&endorsement_b).unwrap();

// Step 4: collect_chunk_endorsements for chunk B → validator's stake is absent
let state = tracker.collect_chunk_endorsements(&chunk_header_b).unwrap();
assert!(!state.is_endorsed); // chunk B fails to reach endorsement threshold
```

### Citations

**File:** core/primitives/src/stateless_validation/chunk_endorsement.rs (L112-117)
```rust
    fn verify(&self, public_key: &PublicKey) -> bool {
        let inner = borsh::to_vec(&self.inner).unwrap();
        let metadata = borsh::to_vec(&self.metadata).unwrap();
        self.signature.verify(&inner, public_key)
            && self.metadata_signature.verify(&metadata, public_key)
    }
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L43-63)
```rust
    pub fn process_chunk_endorsement(&self, endorsement: &ChunkEndorsement) -> Result<(), Error> {
        // Check if we have already received chunk endorsement from this validator.
        let key = endorsement.chunk_production_key();
        let account_id = endorsement.account_id();

        {
            let cache = self.chunk_endorsements.lock();
            if cache.peek(&key).is_some_and(|entry| entry.contains_key(account_id)) {
                tracing::debug!(target: "client", ?endorsement, "already received chunk endorsement");
                return Ok(());
            }
        }

        // Validate the chunk endorsement and store it in the cache.
        match validate_chunk_endorsement(self.epoch_manager.as_ref(), endorsement, &self.store)? {
            ChunkRelevance::Relevant => {
                let mut cache = self.chunk_endorsements.lock();
                cache.get_or_insert_mut(key, || HashMap::new()).insert(
                    account_id.clone(),
                    (endorsement.chunk_hash(), endorsement.signature()),
                );
```

**File:** chain/client/src/stateless_validation/chunk_endorsement.rs (L107-111)
```rust
        let validator_signatures = entry
            .into_iter()
            .filter(|(_, (chunk_hash, _))| chunk_hash == chunk_header.chunk_hash())
            .map(|(account_id, (_, signature))| (account_id, signature.clone()))
            .collect();
```
