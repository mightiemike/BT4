### No vulnerability found for this question.

**Reasoning:** The 20-byte truncation in `StatusCache` is an intentional, documented memory-saving trade-off, not an unpatched bug. `CACHED_KEY_SIZE` is fixed at 20 bytes and the comment states explicitly this is done "to save some memory" [1](#0-0) , and `get_status`/`insert` only ever compare the truncated `KeySlice`, never the full original key [2](#0-1) [3](#0-2) .

For an attacker to exploit this, they would need to produce two distinct transaction signatures (or nonce hashes) that collide on the same 20-byte slice at the same fixed offset while sharing a recent blockhash — this is a generic 160-bit birthday-collision problem (~2^80 work) against values that are themselves outputs of ed25519 signing (not attacker-chosen bit patterns), since the transaction signature is deterministically derived from the signer's private key and message rather than freely settable by the attacker. This computational cost is far outside what "unprivileged attacker, reasonable compute" threat models (and the bounty's feasibility bar) consider reachable, and it does not stem from any accounts-db state manipulation (flush/shrink/ancient-pack/clean/purge) that the question's framing invokes — those mechanisms are unrelated to `StatusCache` key slicing. The `key_index` offset used is `0` for standard-length keys (signatures/hashes ≥ 21 bytes) as set at map creation [4](#0-3) , so the "shared random offset" premise doesn't change the underlying difficulty of the birthday attack.

Because the exploit precondition (finding a genuine 160-bit collision on cryptographically generated signature/hash values) is computationally infeasible with any realistic attacker resource budget, and this is a long-standing accepted design decision rather than an introduced defect, this does not meet the bar for a valid, reproducible finding.

### Citations

**File:** runtime/src/status_cache.rs (L23-24)
```rust
// Only store 20 bytes of the tx keys processed to save some memory.
const CACHED_KEY_SIZE: usize = 20;
```

**File:** runtime/src/status_cache.rs (L152-166)
```rust
        let max_key_index = key.as_ref().len().saturating_sub(CACHED_KEY_SIZE + 1);
        let index = (*index).min(max_key_index);
        let key_slice: &[u8; CACHED_KEY_SIZE] =
            arrayref::array_ref![key.as_ref(), index, CACHED_KEY_SIZE];
        if let Some(stored_forks) = keymap.get(key_slice) {
            let res = stored_forks
                .iter()
                .find(|(f, _)| ancestors.contains_key(f) || self.roots.contains(f))
                .cloned();
            if res.is_some() {
                return res;
            }
        }
        None
    }
```

**File:** runtime/src/status_cache.rs (L217-238)
```rust
        let max_key_index = key.as_ref().len().saturating_sub(CACHED_KEY_SIZE + 1);

        // Get the cache entry for this blockhash.
        let (max_slot, key_index, hash_map) = self
            .cache
            .entry(*transaction_blockhash)
            .or_insert_with(|| (slot, 0, HashMap::new()));

        // Update the max slot observed to contain txs using this blockhash.
        *max_slot = std::cmp::max(slot, *max_slot);

        // Grab the key slice.
        let key_index = (*key_index).min(max_key_index);
        let mut key_slice = [0u8; CACHED_KEY_SIZE];
        key_slice.clone_from_slice(&key.as_ref()[key_index..key_index + CACHED_KEY_SIZE]);

        // Insert the slot and tx result into the cache entry associated with
        // this blockhash and keyslice.
        let forks = hash_map.entry(key_slice).or_default();
        forks.push((slot, res.clone()));

        self.add_to_slot_delta(transaction_blockhash, slot, key_index, key_slice, res);
```
