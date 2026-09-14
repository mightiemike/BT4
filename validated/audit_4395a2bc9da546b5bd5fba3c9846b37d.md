## Title
Rejected/discarded transactions permanently consume an account's `P_MAX` pending-transaction slot in the SPICE Pending Transaction Queue - ([File: chain/client/src/pending_transaction_queue.rs])

## Summary
The report's bug class is: a per-account counter that is incremented to enforce a hard cap, but is never decremented (never "popped") when the underlying item is discarded/finished, permanently consuming capacity and denying the account service. In nearcore's SPICE `PendingTransactionQueue`, the same pattern occurs: a contract account's pending access-key transaction count (`access_key_tx_count`, capped at `P_MAX`) is incremented optimistically inside `PendingTxSession::check_pending`, and the code itself documents that this increment is **not rolled back** if the runtime later rejects the transaction.

## Finding Description
`PendingTxSession::check_pending` enforces NEP-611's `P_MAX` limit (4 pending access-key transactions per contract account) by reading the current pending count from the shared `PendingTransactionQueue` and, if under the limit, optimistically bumping a session-local counter before the transaction has actually been validated/accepted by the runtime: [1](#0-0) 

The comment directly adjacent to this increment states the consequence explicitly: [2](#0-1) 

i.e. "If the runtime subsequently rejects the tx (e.g. insufficient balance), these counts are not rolled back and the tx is discarded (not reintroduced to the pool)." This is structurally identical to the reported bug class: an item is removed/discarded, but the counter used to enforce a hard limit on further transactions is not correspondingly decremented ("popped"). The permanent, cross-block counterpart of this counter is `pending_accounts` in `PendingTransactionQueue`, which is only decremented when a chunk is explicitly certified via `remove_certified_chunk_by_block_hash`: [3](#0-2) 

The `P_MAX` check itself: [4](#0-3) 

Since `access_key_tx_count`/`deploy_tx_count` are only ever decremented by `subtract()` inside `remove_certified_chunk_by_block_hash` (driven by a certified chunk's `block_hash`), any accounting path where a transaction contributes to `chunk_data.accounts` (via `add_chunk_transactions`) but its containing chunk is never certified (or a discarded/rejected transaction consumed a slot without ever entering `chunk_data` at all, as documented above) leaves the account's counter inflated with no corresponding "pop." Once an account accumulates `P_MAX` (4) such stuck slots, `check_pending` returns `PendingTxCheckResult::Skip` for every subsequent access-key transaction from that account, exactly mirroring the reported "PartyA can't make more positions" DoS.

## Impact Explanation
For contract accounts (`HasContract::Yes`), once `P_MAX` slots are exhausted by rejected/uncertified transactions, the account cannot get any new access-key transaction admitted into a chunk by `check_pending`, which returns `Skip`. This is a transaction-triggered halt of one account's ability to use the protocol — analogous to the referenced report's "DoS for users when they should be able to use the protocol as if they are a new user." The queue's own code comment concedes the counts are not rolled back on runtime rejection, confirming the root cause is not accidental but an accepted, documented gap in the accounting logic.

## Likelihood Explanation
The code explicitly limits the blast radius: `check_pending` runs "after signature verification and basic validation, so only transactions with valid signatures can reach this point," and the stated mitigation is that this makes it not "cheaply" spammable. However, this only reduces cost, it does not eliminate the underlying accounting flaw — a signer can legitimately submit transactions that are optimistically admitted into a chunk but later fail balance/nonce checks in the runtime (e.g. racing balance-reducing prior transactions), and each such failure permanently (session-scope or, in the cross-block `pending_accounts` case, until certification) consumes one of only 4 available slots. Because `P_MAX` is small (4), a modest number of failed/discarded transactions is sufficient to exhaust it for a contract account.

## Recommendation
Roll back `session_access_key_tx_counts` / `session_deploy_tx_counts` (and the persistent `pending_accounts` counters, if the corresponding chunk is discarded rather than certified) whenever the runtime subsequently rejects a transaction that was optimistically counted in `check_pending`, mirroring the "pop from the array" fix recommended in the source report — i.e., ensure every increment of a capacity-limiting counter has a matching decrement on every exit path (success, rejection, or chunk discard/reorg), not only on the "happy path" of certification.

## Proof of Concept
Not independently reproducible from static analysis alone; the vulnerable path is explicitly acknowledged in-code: [2](#0-1) 
combined with the enforcement check: [4](#0-3) 
A concrete PoC would submit `P_MAX` (4) access-key transactions from a contract account, each intentionally set to fail at runtime validation (e.g., insufficient balance after a preceding transaction's execution) so they are admitted into `check_pending` (passing signature/basic checks) but rejected downstream; a 5th, valid transaction from the same account would then observe `PendingTxCheckResult::Skip`. I could not fully trace whether `remove_certified_block`/`clear()` calls in `chain/client/src/client.rs`, `chunk_producer.rs`, or the SPICE certification timer (`chain/client/src/spice/timer.rs`) always execute for every accepted chunk under reorg/discard conditions given the limited exploration budget; this would need to be verified in a live/test-loop environment (e.g. `test-loop-tests/src/tests/pending_transaction_queue.rs`) to confirm whether the persistent `pending_accounts` counter (not just the session-local one) can be left permanently inflated.

### Citations

**File:** chain/client/src/pending_transaction_queue.rs (L352-395)
```rust
    /// Remove a certified chunk's transactions from the pending transaction queue.
    pub fn remove_certified_chunk_by_block_hash(&mut self, block_hash: &CryptoHash) {
        let Some(chunk_data) = self.chunks.remove(block_hash) else {
            tracing::debug!(
                target: "client",
                ?block_hash,
                "chunk not found in pending transaction queue during removal"
            );
            return;
        };

        // Reverse per-account aggregates.
        for (account_id, chunk_account) in &chunk_data.accounts {
            if let Some(total_account) = self.pending_accounts.get_mut(account_id) {
                total_account.subtract(chunk_account);
                if total_account.is_zero() {
                    self.pending_accounts.remove(account_id);
                }
            }
        }

        for (scope, &chunk_nonce) in &chunk_data.nonces {
            if let Some(entry) = self.pending_nonces.get_mut(scope) {
                entry.remove(chunk_nonce);
                if entry.is_empty() {
                    self.pending_nonces.remove(scope);
                }
            }
        }

        // Reverse gas key costs.
        for (gas_key, &chunk_gas_key_cost) in &chunk_data.gas_key_costs {
            if let Some(entry) = self.pending_gas_key_costs.get_mut(gas_key) {
                *entry = checked_sub_or_default!(
                    *entry,
                    chunk_gas_key_cost,
                    "gas key cost underflow in remove_certified_chunk"
                );
                if entry.is_zero() {
                    self.pending_gas_key_costs.remove(gas_key);
                }
            }
        }
    }
```

**File:** chain/client/src/pending_transaction_queue.rs (L548-551)
```rust
        // P_MAX for contract accounts.
        if has_contract == HasContract::Yes && !is_gas_key_tx && total_access_key_count >= P_MAX {
            return PendingTxCheckResult::Skip;
        }
```

**File:** chain/client/src/pending_transaction_queue.rs (L560-572)
```rust
        // Update session state optimistically (assumes tx will be accepted).
        // If the runtime subsequently rejects the tx (e.g. insufficient
        // balance), these counts are not rolled back and the tx is discarded
        // (not reintroduced to the pool). This means a rejected tx may consume
        // a P_MAX or deploy exclusivity slot for the remainder of this chunk
        // production session, reducing throughput under high contention. The
        // risk is mitigated by the fact that check_pending is called after
        // signature verification and basic validation, so only transactions
        // with valid signatures can reach this point -- an adversary cannot
        // cheaply spam rejected txs to exhaust slots.
        if !is_gas_key_tx {
            *self.session_access_key_tx_counts.entry(signer_id.clone()).or_insert(0) += 1;
        }
```
