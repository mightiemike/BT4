[1](#0-0) [2](#0-1)

### Citations

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L99-103)
```rust
        let client_known_num_leaves = client_known_version
            .map(|v| v.saturating_add(1))
            .unwrap_or(0);
        let ledger_num_leaves = ledger_version.saturating_add(1);
        Accumulator::get_consistency_proof(self, ledger_num_leaves, client_known_num_leaves)
```

**File:** types/src/proof/accumulator/mod.rs (L67-84)
```rust
    pub fn new(frozen_subtree_roots: Vec<HashValue>, num_leaves: LeafCount) -> Result<Self> {
        ensure!(
            frozen_subtree_roots.len() == num_leaves.count_ones() as usize,
            "The number of frozen subtrees does not match the number of leaves. \
             frozen_subtree_roots.len(): {}. num_leaves: {}.",
            frozen_subtree_roots.len(),
            num_leaves,
        );

        let root_hash = Self::compute_root_hash(&frozen_subtree_roots, num_leaves);

        Ok(Self {
            frozen_subtree_roots,
            num_leaves,
            root_hash,
            phantom: PhantomData,
        })
    }
```
