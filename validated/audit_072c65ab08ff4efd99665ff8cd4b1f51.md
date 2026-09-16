### Title
Quadratic-cost bouncer accounting via full executed-class-hash-set rebuild per transaction - ([File: crates/blockifier/src/bouncer.rs])

### Summary
`Bouncer::try_update`, called once per transaction during block building, computes the marginal contribution of a transaction's executed classes by calling `self.get_executed_class_hashes()`, which reconstructs the *entire* block-level set of executed class hashes accumulated so far, on every call. This mirrors the reported Zebra pattern (rebuild/clone of an entire block-level map on every per-transaction check instead of passing only the transaction's own delta), turning per-transaction bouncer accounting from O(this tx's classes) into O(all distinct classes executed in the block so far), i.e. O(N) per transaction and O(N²) over a block of N transactions that each reference a new class.

### Finding Description
`Bouncer::get_executed_class_hashes` rebuilds a fresh `HashSet<ClassHash>` from the whole accumulated `casm_hash_computation_data_sierra_gas.class_hash_to_casm_hash_computation_gas` map every time it is invoked: [1](#0-0) 

`Bouncer::try_update` — the function invoked once per executed transaction inside the batcher's block-building loop — calls this rebuild method to derive the marginal (new) class hashes contributed by the current transaction: [2](#0-1) 

Because `self.get_executed_class_hashes()` clones and collects the *entire* accumulated set (proportional to the number of distinct classes executed in the block so far) rather than being handed only the current transaction's classes, the cost of this single accounting step grows linearly with block progress. Since this call happens once per transaction, in a block with N transactions that each reference a distinct, previously-unseen class (e.g., N `declare` transactions, or N invokes each triggering a first-time load of a distinct class), the total cost across the block is O(1+2+...+N) = O(N²), exactly the same "rebuild block-level accumulator per transaction" anti-pattern described in the reference report for `remaining_transaction_value`/`utxos.clone()` in Zebra.

The accumulated state (`accumulated_weights.casm_hash_computation_data_sierra_gas`) is then extended with the new transaction's weights via `Bouncer::update`, growing the map that the *next* transaction's `try_update` call will again fully clone: [3](#0-2) 

This bouncer accounting path is invoked directly from the sequencer's normal block-building/transaction-execution flow (`TransactionExecutor`/blockifier execution), which is reachable purely by submitting transactions that get included in a block — no privileged operator/proposer role is required to trigger it.

### Impact Explanation
An attacker who can get many transactions that each introduce a new, distinct class hash included into the same block (e.g., a sequence of `declare` transactions for trivially distinct classes, or invokes each loading a fresh class for the first time) forces the bouncer's per-transaction accounting cost to grow with the number of already-processed transactions in that block, rather than remaining proportional to the new transaction's own footprint. For a block packed with many such transactions, the cumulative extra CPU work in `get_executed_class_hashes()` clones scales quadratically with the transaction count, adding processing latency to block building that is not accounted for by the linear fee/weight model. This degrades block-production throughput/liveness, consistent with the "network unable to confirm new transactions in a timely manner" class of impact, without causing consensus divergence or fund loss.

### Likelihood Explanation
Any unprivileged transaction sender can submit ordinary `declare` or `invoke` transactions that are picked up by the sequencer's normal admission/execution pipeline; no special privilege, node-operator access, or malicious proposer collusion is required. The magnitude of the effect depends on how many distinct classes can practically be packed into one block (bounded by block gas/step limits and per-declare costs), which is smaller in absolute terms than the Zebra worst case (26,000 minimal UTXO-spending transactions), so the practical wall-clock impact is likely materially smaller than the 52-second figure in the reference report; this bounds the severity to Medium rather than higher.

### Recommendation
Change `Bouncer::try_update` to avoid rebuilding the full accumulated executed-class-hash set on every transaction. Maintain a persistent `HashSet<ClassHash>` (or similar) on the `Bouncer`/`TxWeights` accumulator that is updated incrementally (insert only the new transaction's class hashes) instead of being reconstructed via `.keys().cloned().collect()` on every call, so that computing the marginal class hashes for a transaction costs O(that transaction's classes) rather than O(all classes accumulated in the block so far).

### Proof of Concept
1. Submit N `declare_account`/`declare` (or invoke) transactions, each referencing a distinct, previously unseen class hash, so that all N are included sequentially in the same block during batching.
2. During block building, for the k-th such transaction, `Bouncer::try_update` calls `self.get_executed_class_hashes()`, which clones/collects the (k-1)-sized accumulated class-hash map.
3. Summing this cost over the block yields O(N²) hash-map key cloning work solely from bouncer accounting, growing block-building time non-linearly with the number of distinct-class transactions packed into a single block, unlike the linear cost the fee model assumes.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L615-622)
```rust
    pub fn get_executed_class_hashes(&self) -> HashSet<ClassHash> {
        self.accumulated_weights
            .casm_hash_computation_data_sierra_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .cloned()
            .collect()
    }
```

**File:** crates/blockifier/src/bouncer.rs (L627-649)
```rust
    pub fn try_update<S: StateReader>(
        &mut self,
        state_reader: &S,
        tx_state_changes_keys: &StateChangesKeys,
        tx_execution_summary: &ExecutionSummary,
        tx_builtin_counters: &CairoPrimitiveCounterMap,
        tx_resources: &TransactionResources,
        versioned_constants: &VersionedConstants,
        receipt_l2_gas: GasAmount,
    ) -> TransactionExecutorResult<()> {
        // The countings here should be linear in the transactional state changes and execution info
        // rather than the cumulative state attributes.
        let marginal_state_changes_keys =
            tx_state_changes_keys.difference(&self.state_changes_keys);
        let marginal_executed_class_hashes = tx_execution_summary
            .executed_class_hashes
            .difference(&self.get_executed_class_hashes())
            .cloned()
            .collect();
        let n_marginal_visited_storage_entries = tx_execution_summary
            .visited_storage_entries
            .difference(&self.visited_storage_entries)
            .count();
```

**File:** crates/blockifier/src/bouncer.rs (L697-726)
```rust
    fn update(
        &mut self,
        tx_weights: TxWeights,
        tx_execution_summary: &ExecutionSummary,
        state_changes_keys: &StateChangesKeys,
    ) {
        let bouncer_weights = &tx_weights.bouncer_weights;
        let err_msg = format!(
            "Addition overflow. Transaction weights: {bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        self.accumulated_weights.bouncer_weights = self
            .accumulated_weights
            .bouncer_weights
            .checked_add(tx_weights.bouncer_weights)
            .expect(&err_msg);
        self.accumulated_weights
            .casm_hash_computation_data_sierra_gas
            .extend(tx_weights.casm_hash_computation_data_sierra_gas);
        self.accumulated_weights
            .casm_hash_computation_data_proving_gas
            .extend(tx_weights.casm_hash_computation_data_proving_gas);
        self.visited_storage_entries.extend(&tx_execution_summary.visited_storage_entries);
        // Note: cancelling writes (0 -> 1 -> 0) will not be removed, but it's fine since fee was
        // charged for them.
        // Also, `get_patricia_update_resources` relies on this property - each cell must
        // be counted at most once as modified.
        self.state_changes_keys.extend(state_changes_keys);
        self.accumulated_weights.class_hashes_to_migrate.extend(tx_weights.class_hashes_to_migrate);
    }
```
