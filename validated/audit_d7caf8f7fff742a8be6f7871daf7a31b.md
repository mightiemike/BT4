No vulnerability found for this question.

The premise doesn't hold up. `public_key_bytes` is filled via `copy_from_slice(&public_key.to_bytes_compressed()[..])` into a fixed `[u8; 33]` array [1](#0-0) . `copy_from_slice` panics on any length mismatch and a secp256k1 compressed public key is always exactly 33 bytes (1-byte parity prefix + 32-byte x-coordinate) — there is no truncation, no lossy conversion, and no "parsing edge case" that can make two cryptographically distinct public keys collide onto the same 33-byte array. The `signers_by_pk` lookup is exact byte-equality on `[u8; 33]` [2](#0-1) , so two different signer identities can never share a key in that map unless their actual public keys are identical, which would require breaking secp256k1/SHA256 preimage resistance — outside any attacker-reachable code path.

Separately, the reward set itself cannot contain two distinct entries with the same `signing_key`: both PoX-4 (`make_signer_set`, using a `BTreeMap` keyed by `signing_key` to aggregate stacked amounts before emitting one entry per key) [3](#0-2)  and PoX-5 (`pox_5_make_signer_set`, whose test `duplicate_signer_keys_are_aggregated` confirms duplicate keys are aggregated, not duplicated) [4](#0-3)  collapse duplicate signing keys into a single entry with combined weight during reward-set construction, well before `verify_signer_signatures` ever runs.

So the equality being checked — "sum of weights of distinct signer identities vs. threshold" — cannot be broken here: (a) the same signature-recovered public key can only be matched (and removed) once per block, correctly rejecting a genuine second signature from the same key (as already covered by `test_duplicate_signatures`), and (b) the reward set structurally guarantees at most one entry per distinct public key, so there is no second "colliding" slot to double-count into. The scenario described requires either an actual public-key/hash collision (cryptographically infeasible) or a code defect in `to_bytes_compressed`/`copy_from_slice` that does not exist in this codebase.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1126-1131)
```rust
        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1145-1146)
```rust
            let mut public_key_bytes = [0u8; 33];
            public_key_bytes.copy_from_slice(&public_key.to_bytes_compressed()[..]);
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1039-1049)
```rust
        let mut signer_set = BTreeMap::new();
        for entry in entries.iter() {
            let signing_key = entry
                .signer
                .expect("BUG: signing keys should all be set in reward-sets with any signing keys");
            if let Some(existing_entry) = signer_set.get_mut(&signing_key) {
                *existing_entry += entry.amount_stacked;
            } else {
                signer_set.insert(signing_key, entry.amount_stacked);
            };
        }
```

**File:** stackslib/src/chainstate/nakamoto/tests/signer_set.rs (L241-242)
```rust
#[test]
fn duplicate_signer_keys_are_aggregated() {
```
