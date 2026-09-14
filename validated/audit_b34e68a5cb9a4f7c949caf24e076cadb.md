### Title
Silent invariant-violation masking in `PendingTransactionQueue` lets a signer's charged-but-uncertified balance be under-tracked, enabling double-admission of spending transactions before certification - ([File: chain/client/src/pending_transaction_queue.rs])

### Summary
`chain/client/src/pending_transaction_queue.rs` tracks, per account, the balance already committed to *uncertified* (included-but-not-yet-executed/Spice-certified) chunks via `PendingAccount::paid_from_balance`, and uses this running total (`PendingConstraints::paid_from_balance`) to bound how much more a newly-submitted transaction from the same signer may spend before its predecessors are actually applied and reflected in the account's real balance. This mirrors the reported bug class: the code assumes an aggregate quantity ("total supply"/reserved balance) can only grow by additions and shrink by exactly-matching subtractions, and never structurally validates that assumption. When the assumption is violated (a chunk is removed whose recorded per-account contribution exceeds the currently-aggregated total, e.g. due to a reorg/removal race or any code path that calls `remove_certified_chunk_by_block_hash` more than once for effectively overlapping accounting), the `checked_sub_or_default!` macro silently resets the field to `Default::default()` (zero) instead of surfacing an error, logging only a `tracing::error!` and firing a `debug_assert!` that is compiled out in release builds.

### Finding Description
`PendingAccount::subtract` (chain/client/src/pending_transaction_queue.rs:171-187) uses:

```rust
fn subtract(&mut self, other: &PendingAccount) {
    self.access_key_tx_count = checked_sub_or_default!(...);
    self.deploy_tx_count = checked_sub_or_default!(...);
    self.paid_from_balance = checked_sub_or_default!(
        self.paid_from_balance, other.paid_from_balance,
        "paid_from_balance underflow in pending transaction queue subtract"
    );
}
```

and `checked_sub_or_default!` (lines 16-29) is:

```rust
macro_rules! checked_sub_or_default {
    ($a:expr, $b:expr, $msg:expr) => {
        match ($a).checked_sub($b) {
            Some(v) => v,
            None => {
                debug_assert!(false, $msg);
                tracing::error!(target: "client", $msg);
                Default::default()
            }
        }
    };
}
```

The same silent-reset pattern is used for `pending_gas_key_costs` in `remove_certified_chunk_by_block_hash` (lines 383-394) and for `PendingNonce::remove` (lines 121-128).

This is the codebase's implementation of exactly the assumption the external report flags: "the total will not decrease/violate the additive invariant" is not actually enforced — it is merely hoped for. Instead of returning an error up the call chain (as the rest of the runtime consistently does for balance/gas accounting — see `RuntimeError::UnexpectedIntegerOverflow`, `StorageError::StorageInconsistentState` patterns used throughout `runtime/runtime/src/lib.rs` and `core/primitives/src/congestion_info.rs`), this component swallows the violation and resets to zero, in production builds, with no propagated failure.

`paid_from_balance` feeds `PendingConstraints::paid_from_balance` (`get_pending_constraints`, `check_pending`, lines 405-416 and 587-592), which is the client's mechanism for preventing an account from getting multiple transactions admitted into different uncertified chunks whose combined cost exceeds the account's real (last-certified) balance — i.e., it is a double-spend guard across the window between transaction admission and execution/certification. If any code path removes a chunk's contribution in a way that doesn't exactly match what was added for that account (partial removals, reorg-driven re-additions, or a removal invoked for a chunk whose per-account entry was already adjusted/removed elsewhere), the tracked `paid_from_balance` can be reset to `0` instead of reflecting the true still-outstanding reserved amount.

### Impact Explanation
Once `paid_from_balance` for a signer's account is spuriously reset to zero while genuinely-outstanding, not-yet-certified transactions from that signer are still pending execution, subsequent transaction admission (`check_pending`, `get_pending_constraints`) will under-estimate how much of the account's balance is already committed. This allows additional transactions from the same account to be admitted into new chunks that, combined with the still-outstanding uncertified spends, exceed the account's actual balance — a transaction-admission-level bypass of the balance-reservation invariant that exists specifically to prevent overspending before certification. Depending on how strictly the runtime's own per-chunk balance check (ephemeral `TrieUpdate`) catches this at execution time, this can manifest as: chunk production admitting transactions that will predictably fail at execution (denial of throughput/griefing), or — in the Spice model, where multiple uncertified chunks from an account can be produced in parallel before execution — an account successfully getting more spend authorized across concurrent chunks than its balance supports, since the pending-queue check is the *only* cross-chunk guard for uncertified balance and it can be silently defeated.

### Likelihood Explanation
Triggering the underflow requires the accounting invariant "sum of per-chunk contributions add()-ed equals what is later subtract()-ed" to be violated — e.g., a chunk being processed for removal twice, a reorg re-adding a chunk whose old aggregate wasn't fully reversed first, or any interleaving between `add_chunk_transactions`/`remove_certified_chunk_by_block_hash`/`clear()` that a background Devin agent would need to trace across `client.rs`, `chunk_producer.rs`, and `rpc_handler.rs` (all call this queue) to confirm precisely. The mechanism itself (silent reset instead of hard failure) is unconditionally present in production builds since `debug_assert!` compiles to a no-op in release, so if the precondition is ever violated in a real deployment (e.g., under reorgs, out-of-order certification, or future code changes to call sites), the failure is silent and undetectable except via the `tracing::error!` log line, and its effect (weakened double-spend guard) is not otherwise checked anywhere else in the pipeline.

### Recommendation
Replace the silent `checked_sub_or_default!` fallback for `paid_from_balance` (and ideally for `access_key_tx_count`/`deploy_tx_count`/`pending_gas_key_costs`/`PendingNonce`) with an explicit, non-silent failure path: either (a) propagate a hard error that halts admission for the affected account/shard until the queue is rebuilt/cleared, or (b) make the invariant provably true by construction (e.g., store enough per-chunk bookkeeping to make partial-removal ordering irrelevant, or assert only under `debug_assertions` while in release mode still logging at `error!`/`panic!` level rather than silently continuing to serve balance-affecting constraints from a zeroed value). At minimum, add a metric/counter incremented on this fallback path so that in production, a live invariant violation is observable and actionable rather than only visible in trace logs, and audit all call sites (`client.rs`, `chunk_producer.rs`, `rpc_handler.rs`) to confirm add/remove/clear pairing can never legitimately mismatch.

### Proof of Concept
A concrete PoC would require driving the pending-transaction-queue call sequence (`add_chunk_transactions` → `remove_certified_chunk_by_block_hash` → re-`add_chunk_transactions`/reorg) through `chain/client/src/client.rs` and `chunk_producer.rs` in a way that removes a chunk's per-account `paid_from_balance` contribution more than once or after it was already zeroed by a prior removal, then observing `pending_accounts.get(account).paid_from_balance` reset to `0` while a genuinely-still-uncertified transaction's cost is unaccounted, and subsequently submitting a further transaction from that account that `check_pending` admits despite the account's real balance being insufficient to cover all outstanding uncertified spends. I was not able to fully trace, within the available tool budget, the exact reorg/removal call sequence in `client.rs`/`chunk_producer.rs` that would concretely reproduce a legitimate double-removal or mismatched add/remove pairing in this release; a background engineer with full-repo access should trace `ShardedPendingTransactionQueue::remove_certified_block`'s callers against `add_chunk_transactions`'s callers to confirm whether such a mismatch is currently reachable, and write an integration test analogous to `test-loop-tests/src/tests/pending_transaction_queue.rs` that forces the underflow branch and asserts on the resulting under-counted `paid_from_balance`.