The code confirms the vulnerability claim. Here is the analysis:

**Key code path confirmed:**

`ShardedTransactionPool::reintroduce_transactions` at lines 118–123 iterates over transactions to reintroduce and silently discards any that return `NoSpaceLeft`:

```rust
InsertTransactionResult::NoSpaceLeft => 0,
``` [1](#0-0) 

There is no eviction of lower-priority transactions, no bypass of the size limit for reorg reintroduction, and no error propagation to the caller. The dropped transaction is permanently gone from the pool.

`TransactionPool::insert_transaction` enforces the size limit strictly:

```rust
if new_total_transaction_size > limit {
    return InsertTransactionResult::NoSpaceLeft;
}
``` [2](#0-1) 

The limit is applied identically whether the insertion is a fresh user submission or a reorg reintroduction — there is no special path for the latter. [3](#0-2) 

---

### Title
Silent transaction loss during chain reorg when pool is at capacity — (`chain/chunks/src/client.rs`)

### Summary
`ShardedTransactionPool::reintroduce_transactions` silently drops transactions from the abandoned branch when the pool is at `pool_size_limit`. An unprivileged attacker who pre-fills the pool via standard RPC submission can cause legitimate transactions from the reorged branch to be permanently lost, requiring affected users to manually resubmit.

### Finding Description
During a chain reorg, `Client::reintroduce_transactions_for_block` calls `ShardedTransactionPool::reintroduce_transactions` to restore transactions from the abandoned branch back into the mempool. This function calls `pool.insert_transaction(validated_tx)` for each transaction and maps `InsertTransactionResult::NoSpaceLeft` to a count of `0`, silently discarding the transaction with no eviction, no fallback, and no error returned to the caller. [4](#0-3) 

`TransactionPool::insert_transaction` applies `total_transaction_size_limit` uniformly — it does not distinguish between a new user submission and a reorg reintroduction. [5](#0-4) 

If an attacker submits enough transactions to bring the pool to capacity immediately before a reorg is processed, the reintroduction loop will hit `NoSpaceLeft` for legitimate transactions and drop them permanently.

### Impact Explanation
Legitimate transactions that were included in the abandoned branch are permanently removed from the pool. Their senders will never see those transactions included in any future block unless they manually detect the situation and resubmit. This is a non-network-level denial of service against specific users' transactions, fixable without a hardfork by modifying the reintroduction logic.

### Likelihood Explanation
- Reorgs are uncommon in NEAR but do occur.
- The attacker must pay fees to fill the pool, which is a real cost.
- The attacker does not need to cause the reorg — they only need to fill the pool before one naturally occurs, or time their submissions around a known competing fork.
- `pool_size_limit` is a configurable but finite value; filling it is feasible for a motivated attacker.

### Recommendation
In `reintroduce_transactions`, bypass or temporarily relax the size limit, or implement an eviction policy that removes the lowest-priority transactions to make room for reintroduced ones. At minimum, log a warning when a reorg transaction is dropped so operators can detect the condition.

### Proof of Concept
1. Configure a node with a small `pool_size_limit` (e.g., sum of sizes of N attacker transactions).
2. Attacker submits N transactions via RPC, filling the pool to capacity.
3. A chain reorg occurs (naturally or in a test environment).
4. `reintroduce_transactions` is called for the abandoned branch's transactions.
5. Each legitimate transaction hits `NoSpaceLeft` and is silently dropped (`reintroduced_count < txs_to_reintroduce`).
6. Assert that the legitimate transactions are absent from the pool after the reorg — they are, confirming permanent loss. [3](#0-2) [6](#0-5)

### Citations

**File:** chain/chunks/src/client.rs (L111-125)
```rust
    pub fn reintroduce_transactions(
        &mut self,
        shard_uid: ShardUId,
        validated_txs: impl IntoIterator<Item = ValidatedTransaction>,
    ) -> usize {
        let mut reintroduced_count = 0;
        let pool = self.pool_for_shard(shard_uid);
        for validated_tx in validated_txs {
            reintroduced_count += match pool.insert_transaction(validated_tx) {
                InsertTransactionResult::Success | InsertTransactionResult::Duplicate => 1,
                InsertTransactionResult::NoSpaceLeft => 0,
            }
        }
        reintroduced_count
    }
```

**File:** chain/pool/src/lib.rs (L88-107)
```rust
    pub fn insert_transaction(
        &mut self,
        validated_tx: ValidatedTransaction,
    ) -> InsertTransactionResult {
        let tx_hash = validated_tx.get_hash();
        if self.unique_transactions.contains(&tx_hash) {
            return InsertTransactionResult::Duplicate;
        }
        // We never expect the total size to go over `u64` during real operation as that would
        // be more than 10^9 GiB of RAM consumed for transaction pool, so panicking here is intended
        // to catch a logic error in estimation of transaction size.
        let new_total_transaction_size = self
            .total_transaction_size
            .checked_add(validated_tx.wire_size())
            .expect("Total transaction size is too large");
        if let Some(limit) = self.total_transaction_size_limit {
            if new_total_transaction_size > limit {
                return InsertTransactionResult::NoSpaceLeft;
            }
        }
```
