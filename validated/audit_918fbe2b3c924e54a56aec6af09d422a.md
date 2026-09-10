### Title
Transaction-pool sender slot-capacity check blocks legitimate fee-bump replacements once a sender is at `max_account_slots` - (File: `crates/transaction-pool/src/pool/txpool.rs`)

### Summary
`AllTransactions::ensure_valid` rejects a transaction with `PoolErrorKind::SpammerExceededCapacity` whenever the sender's current transaction count is `>= max_account_slots`, without checking whether the incoming transaction is actually a **replacement** of an existing transaction (same nonce) rather than a net-new slot. This is the same bug class as the reported `buyoutLien`/`_createLien` issue: a "don't exceed N active items" check that is only supposed to gate *creation* of new items is instead applied to a *replace* operation, causing legitimate replacements to revert/be rejected once the actor is already at the limit.

### Finding Description
`ensure_valid` performs the sender-slot check before the pool knows whether the transaction will occupy a brand-new slot or replace an existing one: [1](#0-0) 

```rust
fn ensure_valid(...) -> Result<ValidPoolTransaction<T>, InsertErr<T>> {
    if !self.local_transactions_config.is_local(...) {
        let current_txs = self.tx_counter.get(&transaction.sender_id()).copied().unwrap_or_default();
        // Reject transactions if sender's capacity is exceeded.
        // If transaction's nonce matches on-chain nonce always let it through
        if current_txs >= self.max_account_slots && transaction.nonce() > on_chain_nonce {
            return Err(InsertErr::ExceededSenderTransactionsCapacity { .. })
        }
    }
    ...
}
```

`tx_counter` tracks the number of *distinct nonces* (slots) a sender currently occupies in the pool — it is only incremented when a transaction is not a replacement: [2](#0-1) 

The actual replacement detection (`Entry::Occupied` on the same `TransactionId`, with the underpriced/price-bump check) happens later, inside `insert_tx`, *after* `ensure_valid` has already been called and potentially returned an error: [3](#0-2) [4](#0-3) 

So if a sender already occupies exactly `max_account_slots` nonces (e.g. 16 queued/pending transactions with nonces `on_chain_nonce .. on_chain_nonce+15`), and the sender tries to submit a correctly-priced, price-bumped **replacement** for any nonce other than exactly `on_chain_nonce` (i.e. any of the already-occupied, non-first slots), `ensure_valid` computes `current_txs == 16 >= max_account_slots` and `transaction.nonce() > on_chain_nonce`, and unconditionally rejects the replacement as `SpammerExceededCapacity`. The replacement would not have increased the slot count (since `tx_inc` is skipped for replacements), so the rejection is incorrect — the check should only apply to genuinely new nonces, exactly analogous to `_createLien`'s `maxLiens` check firing on `buyoutLien`, which only replaces an existing lien rather than adding one.

### Impact Explanation
This causes pool admission to diverge from what should be valid: a transaction that is otherwise fully valid (correct nonce, sufficient price bump, sufficient balance) and does not increase the sender's outstanding slot usage is nonetheless rejected purely because of transaction ordering (whether `ensure_valid` runs before the replacement check). A sender who is at the account slot limit and has a stuck (underpriced) transaction in one of the non-first nonce slots cannot replace it with a higher-fee transaction to get it mined — they are stuck until the first-nonce transaction clears, even though replacement should always be permitted since it is size-neutral to the sender's own slot bookkeeping. This is a real, deterministic pool-admission-vs-validity divergence: the transaction pool wrongly discards a transaction that the protocol otherwise finds fully admissible.

### Likelihood Explanation
This triggers deterministically any time a busy account (e.g. a bot, exchange hot wallet, or MEV searcher running near the default 16-slot cap) attempts to fee-bump a transaction in a nonce slot other than its lowest pending nonce. No malicious peer or adversarial condition is required — it's a straightforward self-inflicted correctness bug reachable through normal `eth_sendRawTransaction`/pool `add_transaction` usage.

### Recommendation
Move (or add) the slot-capacity check so that it is skipped when the incoming transaction is replacing an existing transaction at the same `TransactionId` (same sender + nonce) — analogous to the Astaria fix of moving the `maxLiens` check out of the shared creation path and into the code path that specifically appends a *new* stack entry. Concretely, `ensure_valid` should look up whether `self.txs.contains_key(&transaction.id())` (or receive that context from `insert_tx`) before applying the `current_txs >= max_account_slots` rejection, mirroring the special-casing already done for `transaction.nonce() == on_chain_nonce` and for `check_delegation_limit`'s `if id == &transaction.transaction_id { return Ok(()) }` replacement carve-out.

### Proof of Concept
1. Set `max_account_slots = 16` (the default, `TXPOOL_MAX_ACCOUNT_SLOTS_PER_SENDER`).
2. From sender `A` with on-chain nonce `N`, submit 16 valid transactions with nonces `N, N+1, ..., N+15`, all accepted (`tx_counter[A] == 16`).
3. Submit a new transaction from `A` with nonce `N+5` (already occupied) with a valid price bump (e.g. +20%) intended to replace the existing one.
4. `ensure_valid` computes `current_txs = 16 >= max_account_slots(16)` and `transaction.nonce() (N+5) > on_chain_nonce (N)`, returning `InsertErr::ExceededSenderTransactionsCapacity`, which surfaces to the caller as `PoolErrorKind::SpammerExceededCapacity`, even though the transaction is a legitimate replacement that does not increase `A`'s slot usage and passes the underpriced/price-bump check performed later in `insert_tx`.

### Citations

**File:** crates/transaction-pool/src/pool/txpool.rs (L1852-1868)
```rust
    fn ensure_valid(
        &self,
        transaction: ValidPoolTransaction<T>,
        on_chain_nonce: u64,
    ) -> Result<ValidPoolTransaction<T>, InsertErr<T>> {
        if !self.local_transactions_config.is_local(transaction.origin, transaction.sender_ref()) {
            let current_txs =
                self.tx_counter.get(&transaction.sender_id()).copied().unwrap_or_default();

            // Reject transactions if sender's capacity is exceeded.
            // If transaction's nonce matches on-chain nonce always let it through
            if current_txs >= self.max_account_slots && transaction.nonce() > on_chain_nonce {
                return Err(InsertErr::ExceededSenderTransactionsCapacity {
                    transaction: Arc::new(transaction),
                })
            }
        }
```

**File:** crates/transaction-pool/src/pool/txpool.rs (L1984-1984)
```rust
        let mut transaction = self.ensure_valid(transaction, on_chain_nonce)?;
```

**File:** crates/transaction-pool/src/pool/txpool.rs (L2046-2078)
```rust
        // try to insert the transaction
        match self.txs.entry(*transaction.id()) {
            Entry::Vacant(entry) => {
                // Insert the transaction in both maps
                self.by_hash.insert(*pool_tx.transaction.hash(), pool_tx.transaction.clone());
                self.tx_type_counts.inc(pool_tx.transaction.transaction.ty());
                entry.insert(pool_tx);
            }
            Entry::Occupied(mut entry) => {
                // Transaction with the same nonce already exists: replacement candidate
                let existing_transaction = entry.get().transaction.as_ref();
                let maybe_replacement = transaction.as_ref();

                // Ensure the new transaction is not underpriced
                if existing_transaction.is_underpriced(maybe_replacement, &self.price_bumps) {
                    return Err(InsertErr::Underpriced {
                        transaction: pool_tx.transaction,
                        existing: *entry.get().transaction.hash(),
                    })
                }
                let new_hash = *pool_tx.transaction.hash();
                let new_transaction = pool_tx.transaction.clone();
                self.tx_type_counts.inc(pool_tx.transaction.transaction.ty());
                let replaced = entry.insert(pool_tx);
                self.tx_type_counts.dec(replaced.transaction.transaction.ty());
                self.by_hash.remove(replaced.transaction.hash());
                self.by_hash.insert(new_hash, new_transaction);

                self.remove_auths(&replaced);

                // also remove the hash
                replaced_tx = Some((replaced.transaction, replaced.subpool));
            }
```

**File:** crates/transaction-pool/src/pool/txpool.rs (L2155-2158)
```rust
        // If this wasn't a replacement transaction we need to update the counter.
        if replaced_tx.is_none() {
            self.tx_inc(inserted_tx_id.sender);
        }
```
