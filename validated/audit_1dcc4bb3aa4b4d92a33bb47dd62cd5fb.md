## Title
Write-set hashing/serialization silently drops hot-state promotions when `hotness_in_epilogue` config and block-epilogue hot-key payload become decoupled - ([File: execution/executor/src/workflow/do_get_execution_output.rs])

## Summary
`by_transaction_execution_unsharded` (and its sharded counterpart) applies `BlockEpilogue` hot-state promotions to a `TransactionOutput`'s `WriteSet` unconditionally, but only upgrades that `WriteSet` from the `V0` on-disk format to the `V1` format when a separate on-chain config flag (`hotness_in_epilogue()`) is set. `WriteSetV0` marks its `hotness` field `#[serde(skip)]`, so whenever a `V0`-wrapped write set carries non-empty hotness but is not converted to `V1`, that hotness data is invisible to both the BCS serializer and the `CryptoHash` used to build `state_change_hash` in `TransactionInfo`. Because the gating flag is read fresh from on-chain config at commit/replay time rather than being intrinsically tied to whether the epilogue payload actually contains hot keys, a config flip between the block's original execution and a later replay (or between validators with different config visibility) causes the recomputed `state_change_hash`/write-set bytes to diverge from what was originally committed to the transaction accumulator.

## Finding Description
In `by_transaction_execution_unsharded`: [1](#0-0) 

`output.add_hotness(...)` is called unconditionally for every `Transaction::BlockEpilogue`, populating the write set's `hotness: BTreeSet<StateKey>` field. Immediately after, `Self::convert_write_sets_to_v1(&mut transaction_outputs)` — which calls `WriteSet::into_v1()` to upgrade the write set's on-disk representation — is only invoked `if onchain_config.hotness_in_epilogue()`.

The two representations differ critically in how `hotness` is serialized: [2](#0-1) 

`WriteSetV0::hotness` and `WriteSetV0::extensions` are `#[serde(skip)]`, while `WriteSetV1` serializes both fields. Since `WriteSet` derives `BCSCryptoHash`, the crypto hash of a `V0`-wrapped write set with non-empty `hotness` is bitwise identical to the same write set with empty `hotness` — the promotion data is cryptographically invisible.

This hash feeds directly into the immutable, accumulator-committed `TransactionInfo`: [3](#0-2) 

and the raw bytes are what gets persisted to the write-set DB and later returned/replayed: [4](#0-3) 

Because `onchain_config.hotness_in_epilogue()` is evaluated independently at whatever time execution/replay happens (it is read from live on-chain configuration, not frozen with the original block), any scenario where the flag's value differs between the time a block was originally committed and a later re-derivation of the same block (replay, re-execution during chunk-based state sync via `by_transaction_output`, or state-sync/backup restore verification) results in a different write set representation (`V0` vs `V1`) for the *same* logical `BlockEpilogue` output. If the epilogue's `try_get_keys_to_make_hot()` payload is non-empty in that block (which can happen if the flag was on when the block executed and thus `hotness_in_epilogue` gated payload construction, but is off during a later replay attempt, or vice versa on a validator that hasn't yet activated the feature), the recomputed `state_change_hash` will not match the value already baked into the accumulator, breaking transaction-info hash determinism.

## Impact Explanation
`state_change_hash` is a component of `TransactionInfo`, which is hashed into the transaction accumulator that backs the ledger's root of trust (`ledger_info.transaction_accumulator_hash()`), verified via `TransactionAccumulatorProof`/`TransactionInfoWithProof::verify` throughout the codebase (e.g. `types/src/proof/definition.rs`, `types/src/transaction/mod.rs`'s `TransactionWithProof::verify`). Any divergence in this hash across independent computations of the same historical block is a state-commitment/replay integrity break: replaying, restoring from backup, or re-verifying a block whose config visibility differs from origin would either fail proof verification or (if the mismatched write set is what actually gets applied to storage) silently drop hot-state promotion writes from the durable ledger, producing state that differs from the originally-committed VM result. This falls squarely under the required "hard-fork-only divergence during commit, replay, restore" impact category.

## Likelihood Explanation
The trigger requires the `hotness_in_epilogue` on-chain config to be read differently at two points in time for the same block (e.g., feature-flag toggled by governance around the time it's enabled, a node executing during an upgrade window, or backup/restore/re-execution paths reading current config instead of the block's original config) while the epilogue still carries non-empty hot keys. This is a narrow, config-transition-dependent window rather than a routinely-triggerable bug, but the underlying invariant break (asymmetric field skipping between `WriteSetV0`/`WriteSetV1` combined with unconditional field population vs. conditional format upgrade) is a genuine local code defect independent of any external report.

## Recommendation
Tie the `add_hotness` call and the `V0`→`V1` conversion to the same condition, or make `convert_write_set_to_v1` (or equivalent) unconditional whenever hotness is non-empty, e.g.:
```diff
 for (transaction, output) in transactions.iter().zip_eq(transaction_outputs.iter_mut()) {
     if let Transaction::BlockEpilogue(payload) = transaction {
         assert!(output.status().is_kept(), "Block epilogue must be kept");
-        output.add_hotness(
-            payload.try_get_keys_to_make_hot().cloned().unwrap_or_default(),
-        );
+        let hot_keys = payload.try_get_keys_to_make_hot().cloned().unwrap_or_default();
+        if !hot_keys.is_empty() {
+            output.add_hotness(hot_keys);
+            output.convert_write_set_to_v1(); // force V1 whenever hotness is non-empty
+        }
     }
 }
-if onchain_config.hotness_in_epilogue() {
-    Self::convert_write_sets_to_v1(&mut transaction_outputs);
-}
```
Additionally, consider removing the `#[serde(skip)]` asymmetry, or asserting at serialization time that a `V0` write set never carries non-empty `hotness`/`extensions`, so a similar mistake fails loudly rather than silently corrupting the hash/bytes.

## Proof of Concept
Not independently executed (no sandbox access in this investigation); the divergence is demonstrated structurally:
1. Construct a `BlockEpilogue` transaction whose payload's `try_get_keys_to_make_hot()` returns a non-empty set.
2. Execute the block with `onchain_config.hotness_in_epilogue() == true`; `add_hotness` sets the field and `convert_write_sets_to_v1` upgrades to `WriteSetV1`. `CryptoHash::hash(&write_set)` includes the hotness bytes, producing hash `H1`, which is committed into `TransactionInfo.state_change_hash` and the accumulator.
3. Re-derive/replay the identical `TransactionOutput` object (same in-memory hotness set) but with `onchain_config.hotness_in_epilogue() == false` (e.g., a governance-driven flag flip, or a validator/backup-verifier reading a different config snapshot); `add_hotness` still executes but the `V0`→`V1` conversion is skipped. `CryptoHash::hash(&write_set)` on the still-`WriteSetV0` value produces hash `H0` that omits the hotness bytes (due to `#[serde(skip)]`), where `H0 != H1`.
4. Any consumer needing to re-derive `TransactionInfo` for this block (replay, backup restore verification, `TransactionOutputListWithProof::verify`) obtains `H0` instead of the originally accumulated `H1`, causing proof/replay verification failure or, if written directly to storage, a durable write-set schema entry lacking the hotness data present in the original committed transaction.

### Citations

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L159-172)
```rust
        for (transaction, output) in transactions.iter().zip_eq(transaction_outputs.iter_mut()) {
            if let Transaction::BlockEpilogue(payload) = transaction {
                assert!(output.status().is_kept(), "Block epilogue must be kept");
                output.add_hotness(
                    payload
                        .try_get_keys_to_make_hot()
                        .cloned()
                        .unwrap_or_default(),
                );
            }
        }
        if onchain_config.hotness_in_epilogue() {
            Self::convert_write_sets_to_v1(&mut transaction_outputs);
        }
```

**File:** types/src/write_set.rs (L792-827)
```rust
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV0 {
    value_writes: WriteSetMut,
    /// Hot state promotions, non-empty only in block epilogues.
    #[serde(skip)]
    hotness: BTreeSet<StateKey>,
    /// Opt-in side-channels (see [`Extension`]). Skipped from serde so `TransactionInfo` hashes and
    /// the on-disk WriteSet format are unaffected.
    #[serde(skip)]
    extensions: Vec<Extension>,
}

impl WriteSetV0 {
    #[inline]
    pub fn iter(&self) -> btree_map::Iter<'_, StateKey, WriteOp> {
        self.value_writes.write_set.iter()
    }

    #[inline]
    pub fn into_write_op_iter(self) -> btree_map::IntoIter<StateKey, WriteOp> {
        self.value_writes.write_set.into_iter()
    }

    pub fn get(&self, key: &StateKey) -> Option<&WriteOp> {
        self.value_writes.get(key)
    }
}

/// Like [`WriteSetV0`], but serializes the hotness and extension
/// buckets alongside the value write set.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct WriteSetV1 {
    value_writes: WriteSetMut,
    hotness: BTreeSet<StateKey>,
    extensions: Vec<Extension>,
}
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L90-90)
```rust
                let write_set_hash = CryptoHash::hash(txn_output.write_set());
```

**File:** storage/aptosdb/src/ledger_db/write_set_db.rs (L138-144)
```rust
                chunk.iter().enumerate().try_for_each(|(i, txn_out)| {
                    Self::put_write_set(
                        chunk_first_version + i as Version,
                        txn_out.write_set(),
                        &mut batch,
                    )
                })?;
```
