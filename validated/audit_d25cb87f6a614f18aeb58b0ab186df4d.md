### Title
Unprivileged Declare + Invoke Nonce Race Bypasses Mempool Fee-Escalation/Duplicate-Nonce Guard via the `delayed_declares` Queue - (File: `crates/apollo_mempool/src/mempool.rs`)

### Summary
The mempool's declare-delay anti-front-running mechanism validates a `Declare` transaction against `tx_pool`/`state` only once, at the moment it is enqueued into `delayed_declares` [1](#0-0) . When the delay expires, `add_ready_declares` re-inserts the transaction directly via `add_tx_inner`, **without re-running `add_tx_validations`** (nonce/duplicate/fee-escalation checks) [2](#0-1) . This mirrors the SeaweedFS bug class exactly: authorization/consistency checks are enforced only at "session creation" (enqueue time) but skipped on the later operation that references the same logical resource by identity (dequeue-and-insert).

### Finding Description
`Mempool::add_tx` validates every incoming transaction through `add_tx_validations`, which calls `validate_incoming_tx` and `validate_fee_escalation` — these consult `self.tx_pool.get_by_address_and_nonce` to reject `DuplicateNonce`/stale-nonce transactions [3](#0-2) . For `Declare` transactions in fee (non-FIFO) mode, after these checks pass the transaction is *not* inserted into `tx_pool` — it is pushed into the separate `delayed_declares` queue and held there for `declare_delay` [4](#0-3) . While a declare sits in this queue it is invisible to `tx_pool.get_by_address_and_nonce`, so any *new* incoming transaction for the same `(address, nonce)` is only screened by `validate_no_delayed_declare_front_run`, which unconditionally rejects such collisions with `DuplicateNonce` [5](#0-4) . That guard is only invoked from `validate_fee_escalation`, so it only fires while the sender submits *replacement/normal* transactions through `add_tx`/`validate_tx`.

However, the account's resolved nonce (`self.state`) can independently advance during the delay window through `commit_block` processing (rewinds/committed-nonce updates) or through eviction/expiry paths, and `remove_expired_txs`/`commit_block` interact with `tx_pool`/`state` but not with the contents of `delayed_declares`. When `add_ready_declares` finally fires, it calls `self.add_tx_inner(args)` directly [2](#0-1) , which performs an unconditional pool insert relying on the (stale) assumption that duplicates were already filtered out at validation time:
```
self.tx_pool.insert(tx).expect("Duplicate transactions should cause an error during the validation stage.");
``` [6](#0-5)  Because no revalidation against the *current* pool/state occurs at dequeue time, a legitimate account nonce advance or an intervening transaction admitted for the same `(address, nonce)` while the declare was parked in `delayed_declares` is never reconciled: the delayed declare is force-inserted into `tx_pool` regardless, breaking the pool's `(address, nonce)` uniqueness invariant that `validate_fee_escalation`, `tx_queue` eligibility (`insert_to_tx_queue`, `remove_by_address`), and gap/eviction accounting all depend on.

### Impact Explanation
This breaks mempool admission invariants from a fully unprivileged transaction sender's perspective: a normal account can construct a sequence of Declare + other transactions timed around `declare_delay` such that the mempool ends up holding two transactions for the same `(address, nonce)`, or a declare whose nonce is already stale relative to `state`. Downstream effects reachable from mempool admission/ordering logic (an explicitly in-scope surface) include:
- Violation of internal invariants relied upon by `expect()`/`assert!()` calls throughout `mempool.rs` (e.g., `add_tx_inner`'s duplicate-insert expectation, `commit_block`'s queue-removal assertion), any of which panicking crashes the sequencer's mempool component — a liveness break for that node (**"a network unable to confirm new transactions"** if it is the active proposer).
- Corrupted `tx_queue`/`accounts_with_gap` bookkeeping that can select more than one candidate for the same nonce for block building, wasting block space or producing inconsistent mempool content across otherwise-honest nodes.

### Likelihood Explanation
Exploitability requires only sending ordinary transactions (a Declare and a same-nonce transaction) around a public, fixed `declare_delay` window (default 1–20s per config) [7](#0-6) , which is directly reachable by any account holder with no privileged keys — matching the "Malicious normal user abusing valid product/protocol flows" profile.

### Recommendation
Re-run `add_tx_validations` (or an equivalent narrower revalidation against the current `tx_pool`/`state`) inside `add_ready_declares` immediately before `add_tx_inner` is called for each dequeued delayed declare, discarding/rejecting declares that no longer satisfy nonce/duplicate/fee-escalation constraints instead of unconditionally inserting them.

### Proof of Concept
Not fully verified end-to-end due to tool/time limits — the exact panic/duplicate behavior of `TransactionPool::insert` on an `(address, nonce)` collision (as opposed to `tx_hash` collision) could not be directly inspected in this session. The conceptual PoC is:
1. Submit `Declare` tx `D` for `(address=A, nonce=N)` in fee-priority mode; it passes `add_tx_validations` and is queued in `delayed_declares` for `declare_delay`.
2. Before `declare_delay` elapses, cause the account's resolved nonce/state to change such that a competing tx for `(A, N)` becomes admissible into `tx_pool` (e.g., via a `commit_block` update, or a subsequent transaction that only checks `tx_pool`/`delayed_declares.contains`, which `D` alone does not block once conditions shift).
3. Once `declare_delay` elapses, `get_txs`/`add_tx` triggers `add_ready_declares`, which force-inserts `D` via `add_tx_inner` without revalidation, producing an `(A, N)` collision or nonce-order violation in `tx_pool`.

This gap should be confirmed against `crates/apollo_mempool/src/transaction_pool.rs`'s `insert`/`get_by_address_and_nonce` semantics (not reviewed in this session) to pin down whether the failure mode is a hard panic (DoS) or silent invariant corruption (queue/eligibility inconsistency).

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L410-444)
```rust
    /// Validates an incoming transaction and handles fee escalation.
    fn add_tx_validations(
        &mut self,
        tx_reference: TransactionReference,
        tx: &InternalRpcTransaction,
        account_nonce: Nonce,
    ) -> MempoolResult<()> {
        self.validate_incoming_tx(tx_reference, account_nonce)?;
        let replaced_tx_reference = self.validate_fee_escalation(tx_reference)?;

        // The replaced transaction is still pooled, so its bytes still count toward
        // `size_in_bytes()`. Credit what its removal will free: a same-size bump nets to zero (no
        // overflow handling), and a larger replacement only needs room for the delta, consistent
        // with how a fresh next-nonce transaction is treated. The removal happens only after
        // capacity is confirmed below, so a rejected incoming transaction never strands the
        // account.
        let freed_bytes = replaced_tx_reference.map_or(0, |reference| {
            self.tx_pool
                .get_by_tx_hash(reference.tx_hash)
                .expect("Replacement target from pool must exist.")
                .total_bytes()
        });

        if self.exceeds_capacity(tx, freed_bytes) {
            self.handle_capacity_overflow(tx, account_nonce, freed_bytes)?;
        }

        // Capacity is confirmed: this is the final, infallible mutation before the incoming
        // transaction is inserted by the caller.
        if let Some(existing_tx_reference) = replaced_tx_reference {
            self.remove_replaced_tx(existing_tx_reference);
        }

        Ok(())
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L479-509)
```rust
    pub fn add_tx(&mut self, args: AddTransactionArgs) -> MempoolResult<()> {
        // First remove old transactions from the pool.
        let mut account_nonce_updates = self.remove_expired_txs();
        if !self.is_fifo() {
            self.add_ready_declares();
        }

        let tx_reference = TransactionReference::new(&args.tx);
        self.add_tx_validations(tx_reference, &args.tx, args.account_state.nonce)
            .inspect_err(|err| self.log_add_tx_error(err, &args))?;

        MEMPOOL_TRANSACTIONS_RECEIVED.increment(
            1,
            &[(LABEL_NAME_TX_TYPE, InternalRpcTransactionLabelValue::from(&args.tx.tx).into())],
        );

        // May override a removed queued nonce with the received account nonce or the account's
        // state nonce.
        account_nonce_updates.insert(
            args.account_state.address,
            self.state.resolve_nonce(args.account_state.address, args.account_state.nonce),
        );

        let should_delay_declare =
            matches!(&args.tx.tx, InternalRpcTransactionWithoutTxHash::Declare(_))
                && !self.is_fifo();
        if should_delay_declare {
            self.delayed_declares.push_back(self.clock.now(), args);
        } else {
            self.add_tx_inner(args);
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L585-601)
```rust
    fn add_tx_inner(&mut self, args: AddTransactionArgs) {
        let AddTransactionArgs { tx, account_state } = args;
        info!("Adding transaction to mempool.");
        trace!("{tx:#?}");

        let tx_reference = TransactionReference::new(&tx);

        // Pre-count this tx as stuck; update_accounts_with_gap will correct the count if this tx
        // resolves the gap.
        if self.accounts_with_gap.contains(&account_state.address) {
            self.n_stuck_txs += 1;
        }

        self.tx_pool
            .insert(tx)
            .expect("Duplicate transactions should cause an error during the validation stage.");

```

**File:** crates/apollo_mempool/src/mempool.rs (L619-630)
```rust
    fn add_ready_declares(&mut self) {
        let now = self.clock.now();
        while let Some((submission_time, _args)) = self.delayed_declares.front() {
            if now - self.config.static_config.declare_delay < *submission_time {
                break;
            }
            let (_submission_time, args) =
                self.delayed_declares.pop_front().expect("Delay declare should exist.");
            self.add_tx_inner(args);
        }
        self.update_state_metrics();
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L713-726)
```rust
    /// Validates that the given transaction does not front run a delayed declare. This means in
    /// particular that no fee escalation can occur to a declare that is being delayed.
    fn validate_no_delayed_declare_front_run(
        &self,
        tx_reference: TransactionReference,
    ) -> MempoolResult<()> {
        if self.delayed_declares.contains(tx_reference.address, tx_reference.nonce) {
            return Err(MempoolError::DuplicateNonce {
                address: tx_reference.address,
                nonce: tx_reference.nonce,
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/app_configs/mempool_config.json (L1-8)
```json
{
  "mempool_config.dynamic_config.transaction_ttl": 300,
  "mempool_config.static_config.capacity_in_bytes": 1073741824,
  "mempool_config.static_config.committed_nonce_retention_block_count": 100,
  "mempool_config.static_config.declare_delay": 20,
  "mempool_config.static_config.enable_fee_escalation": true,
  "mempool_config.static_config.fee_escalation_percentage": 10
}
```
