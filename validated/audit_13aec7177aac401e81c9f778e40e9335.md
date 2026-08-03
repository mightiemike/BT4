[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L128-172)
```rust
    /// Returns the root hash at given `version`.
    pub fn get_root_hash(&self, version: Version) -> Result<HashValue> {
        if let Some(hash) = self
            .db
            .get::<TransactionAccumulatorRootHashSchema>(&version)?
        {
            return Ok(hash);
        }
        Accumulator::get_root_hash(self, version + 1).map_err(Into::into)
    }

    /// Deletes the transaction accumulator between a range of version in [begin, end).
    ///
    /// To avoid always pruning a full left subtree, we uses the following algorithm.
    /// For each leaf with an odd leaf index.
    /// 1. From the bottom upwards, find the first ancestor that's a left child of its parent.
    /// (the position of which can be got by popping "1"s from the right of the leaf address).
    /// Note that this node DOES NOT become non-useful.
    /// 2. From the node found from the previous step, delete both its children non-useful, and go
    /// to the right child to repeat the process until we reach a leaf node.
    /// More details are in this issue https://github.com/aptos-labs/aptos-core/issues/1288.
    pub(crate) fn prune(begin: Version, end: Version, db_batch: &mut SchemaBatch) -> Result<()> {
        for version_to_delete in begin..end {
            db_batch.delete::<TransactionAccumulatorRootHashSchema>(&version_to_delete)?;
            // The even version will be pruned in the iteration of version + 1.
            if version_to_delete % 2 == 0 {
                continue;
            }

            let first_ancestor_that_is_a_left_child =
                Self::find_first_ancestor_that_is_a_left_child(version_to_delete);

            // This assertion is true because we skip the leaf nodes with address which is a
            // a multiple of 2.
            assert!(!first_ancestor_that_is_a_left_child.is_leaf());

            let mut current = first_ancestor_that_is_a_left_child;
            while !current.is_leaf() {
                db_batch.delete::<TransactionAccumulatorSchema>(&current.left_child())?;
                db_batch.delete::<TransactionAccumulatorSchema>(&current.right_child())?;
                current = current.right_child();
            }
        }
        Ok(())
    }
```
