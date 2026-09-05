## Analysis

**Equality claimed broken:** `verify_proof`'s final check `*root_hash == trie_hash` for fork B should only succeed for `(key, value)` pairs actually committed under fork B's root, but the claim is that skipping the `TrieHash::from_data_array` mixing step in `verify_shunt_proof_head` when `hashes.is_empty()` [1](#0-0)  lets a proof generated against fork A's first block be verified as valid against fork B's `root_hash`/`root_to_block`.

**Tracing both sides of the equality:**

The "special case" in `verify_shunt_proof_head` is not a deviation from the real root-hash computation — it is the verifier faithfully reproducing it. The actual MARF root hash computation in `Trie::get_trie_root_hash` does exactly the same thing:

```rust
pub fn get_trie_root_hash<T: MarfTrieId>(
    storage: &mut TrieStorageConnection<T>,
    children_root_hash: &TrieHash,
) -> Result<TrieHash, Error> {
    let hashes = Trie::get_trie_root_ancestor_hashes_bytes(storage, children_root_hash)?;
    match hashes.as_slice() {
        [single_hash] => Ok(*single_hash),
        multiple_hashes => Ok(TrieHash::from_data_array(multiple_hashes)),
    }
}
``` [2](#0-1) 

When a trie has zero ancestors (first block of a fork), `get_trie_root_ancestor_hashes_bytes` returns a single-element vector (just the node's own root hash), and the *real* root hash used when the trie was actually committed is that single hash, unmixed [3](#0-2) . `verify_shunt_proof_head`'s `hashes.is_empty()` branch returns `*node_root_hash` directly for precisely this same zero-ancestor case, matching the real computation bit-for-bit [4](#0-3) . This is confirmed as intentional consistent design, not an omitted mixing step — there is nothing to mix when there are no ancestors.

**Why the described exploit fails:**

For the attack to work, fork A's genesis-block trie's real root hash (a value derived from `sha512trunc256` over the leaf/node contents of A's first block) would have to collide with fork B's later block's real root hash under `root_to_block` — i.e., an actual preimage/second-preimage collision on the hash function used by `get_node_hash`/`TrieHash`, not a weakness introduced by the "skip mixing" branch. The skip-mixing branch does not lower any collision-resistance bar: both the "no ancestors" case and the "with ancestors" case rely on the same cryptographic hash for their respective root computations. Additionally, `root_to_block` is explicitly documented as bijective by design, relying on the same collision resistance [5](#0-4) . There is no code path here that an unprivileged attacker (who cannot forge hash collisions) can exploit to make a proof from fork A verify successfully against fork B's root — it would require breaking the underlying hash function, which is out of scope per the audit rules (cryptographic collision attacks are not a codebase logic defect).

## No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/stacks/index/proofs.rs (L705-748)
```rust
    /// Verify the head of a shunt proof
    fn verify_shunt_proof_head(
        node_root_hash: &TrieHash,
        shunt_proof_head: &TrieMerkleProofType<T>,
    ) -> Option<TrieHash> {
        // ancestor hashes are always the first item
        let hash = match shunt_proof_head {
            TrieMerkleProofType::Shunt((ref idx, ref hashes)) => {
                if *idx != 0 {
                    trace!("First shunt proof entry must have idx == 0");
                    return None;
                }

                if hashes.is_empty() {
                    // special case -- if this shunt proof has no hashes (i.e. this is a leaf from the first
                    // block), then we can safely skip this step
                    trace!(
                        "Special case for a 0-ancestor node: hash is just the trie hash: {:?}",
                        node_root_hash
                    );
                    *node_root_hash
                } else {
                    let mut all_hashes = Vec::with_capacity(hashes.len() + 1);
                    all_hashes.push(*node_root_hash);
                    for h in hashes {
                        all_hashes.push(*h);
                    }
                    let ret = TrieHash::from_data_array(&all_hashes);
                    trace!(
                        "Shunt proof head: hash = {:?}, all_hashes = {:?}",
                        &ret,
                        &all_hashes
                    );
                    ret
                }
            }
            _ => {
                trace!("Shunt proof head is not a shunt proof node");
                return None;
            }
        };

        Some(hash)
    }
```

**File:** stackslib/src/chainstate/stacks/index/proofs.rs (L1099-1102)
```rust
    /// For the proof validation to work, the verifier needs to know which Trie roots correspond to
    /// which block headers.  This can be calculated and verified independently from the blockchain
    /// headers.
    /// NOTE: Trie root hashes are globally unique by design, even if they represent the same contents, so the root_to_block map is bijective with high probability.
```

**File:** stackslib/src/chainstate/stacks/index/trie.rs (L856-875)
```rust
    /// Calculate the bytes of the ancestor root hashes of this trie, plus the current trie's root.
    /// Return the resulting sequence of hashes a a single byte buffer.
    pub fn get_trie_root_ancestor_hashes_bytes<T: MarfTrieId>(
        storage: &mut TrieStorageConnection<T>,
        children_root_hash: &TrieHash,
    ) -> Result<Vec<TrieHash>, Error> {
        trace!(
            "Calculate Trie hash from root node digest {:?}",
            children_root_hash
        );
        let mut ancestor_bytes = Trie::get_trie_ancestor_hashes_bytes(storage)?;
        ancestor_bytes.insert(0, *children_root_hash);

        trace!(
            "Trie ancestor bytes for root hash calculation: {:?}",
            &ancestor_bytes
        );

        Ok(ancestor_bytes)
    }
```

**File:** stackslib/src/chainstate/stacks/index/trie.rs (L880-888)
```rust
    pub fn get_trie_root_hash<T: MarfTrieId>(
        storage: &mut TrieStorageConnection<T>,
        children_root_hash: &TrieHash,
    ) -> Result<TrieHash, Error> {
        let hashes = Trie::get_trie_root_ancestor_hashes_bytes(storage, children_root_hash)?;
        match hashes.as_slice() {
            [single_hash] => Ok(*single_hash),
            multiple_hashes => Ok(TrieHash::from_data_array(multiple_hashes)),
        }
```
