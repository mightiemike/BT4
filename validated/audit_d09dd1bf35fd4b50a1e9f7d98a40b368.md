### Title
Mempool capacity DoS via perpetual fee-escalation renewal of eviction-exempt "gapped" transactions - (File: `crates/apollo_mempool/src/mempool.rs`, `crates/apollo_mempool/src/transaction_pool.rs`)

### Summary
An unprivileged transaction sender can permanently reserve mempool capacity by submitting a transaction with a future (gapped) nonce and periodically replacing it via fee escalation just before its TTL expires. Each fee-escalation replacement is inserted as a brand-new pool entry with a freshly reset submission timestamp, so the transaction never ages out via TTL. Because "gapped" accounts (whose lowest pool nonce exceeds the account nonce) are treated as ineligible for capacity-based eviction, this reserved slot can never be reclaimed by the mempool to make room for legitimate transactions — mirroring the reported pattern of "closing and reopening before expiration" to indefinitely occupy a finite, shared resource.

### Finding Description
The mempool bounds total occupied space via `MempoolStaticConfig::capacity_in_bytes` [1](#0-0) , and reclaims space from expired transactions using a submission-time TTL:

```
fn remove_expired_txs(&mut self) -> AddressToNonce {
    let removed_txs = self.tx_pool.remove_txs_older_than(self.config.dynamic_config.transaction_ttl, &self.state.staged);
``` [2](#0-1) 

The TTL clock is keyed on `submission_time`, which is set fresh on every `insert`, including inserts that occur when a transaction is *replaced* by fee escalation:

```
fn insert(&mut self, tx: TransactionReference) -> Option<SubmissionID> {
    let submission_id = SubmissionID { submission_time: self.clock.now(), tx_hash: tx.tx_hash, batching_time: None };
``` [3](#0-2) 

Fee escalation is exposed to any account: `validate_fee_escalation` / `should_replace_tx` allow an incoming transaction at the same `(address, nonce)` to replace an existing one as long as tip and max L2 gas price are bumped by `fee_escalation_percentage` (default 10%) [4](#0-3) . `add_tx_validations` then removes the old transaction and the caller inserts the new one, which — per the code above — gets a brand new `submission_time = now` [5](#0-4) .

Critically, capacity-based eviction explicitly refuses to evict "gapped" accounts (accounts whose lowest pool nonce is above the account's current nonce), as confirmed by the mempool's own test suite:

```
fn fee_escalation_rejected_replacement_of_gapped_tx_keeps_existing_tx() {
    // Existing tx A sits at a future nonce (5) while the account nonce is 0, so A is gapped. A
    // gapped account is never granted eviction, so a larger replacement is rejected.
``` [6](#0-5) 

Combining these two facts: a transaction sitting at a future nonce (a permanent "gap", since the attacker never submits the missing lower-nonce transaction) is (a) never selected as an eviction candidate when the pool is full, and (b) can have its TTL clock reset indefinitely by replacing it with a marginally higher fee just before expiry — the replacement is itself inserted at the same gapped nonce, so it remains gapped and thus remains eviction-exempt. This lets an attacker occupy `capacity_in_bytes` worth of mempool space forever, without ever contributing a valid, executable, or committable transaction (since a permanent nonce gap means it is never dequeued for `get_txs`) [7](#0-6) . Once enough addresses do this to fill the configured capacity, new legitimate transactions from other users are rejected with `MempoolFull` [8](#0-7) .

I could not retrieve the exact body of the capacity-overflow eviction-candidate-selection routine (referenced by `exceeds_capacity`/`handle_capacity_overflow` in `mempool.rs`) within the available tool budget; the eviction-exemption for gapped accounts is established here via the explicit code comment and passing test in `fee_mempool_test.rs`, not by direct inspection of `handle_capacity_overflow`'s internals. This should be verified directly against that function before treating the root cause as fully confirmed.

### Impact Explanation
If confirmed, this allows a single unprivileged account (or a small number of low-cost, cheap-to-front, sybil accounts, since gas cost of holding a gapped tx is only the L1/L2 fee needed to satisfy the 10% escalation bump, never actually executed) to permanently consume all `capacity_in_bytes` of mempool space across the network's gateway nodes, causing legitimate transactions to be rejected with `MempoolFull`. This is a network-wide "unable to confirm new transactions" condition — a Medium/High severity liveness/DoS impact on the sequencer's transaction admission path.

### Likelihood Explanation
The attack requires only: (1) knowledge of the mempool's `transaction_ttl` and `fee_escalation_percentage` (both public config values, visible in `crates/apollo_node/resources/config_schema.json`), and (2) periodically resubmitting a fee-bumped, future-nonce transaction before TTL expiry — an action fully reachable via the public gateway `add_tx` path available to any transaction sender. No special privileges, timing races, or non-deterministic conditions are needed, making this highly likely to be exploitable if the eviction-exemption for gapped accounts holds as the test suggests.

### Recommendation
- Do not treat "gapped" (future-nonce) transactions as unconditionally eviction-exempt; allow eviction of long-lived gapped transactions when the pool is at capacity and a legitimate transaction needs room.
- When a transaction is replaced via fee escalation, preserve (do not reset) its original `submission_time` for TTL purposes, or otherwise cap the number of consecutive fee-escalation renewals / total lifetime a single logical "slot" (address+nonce lineage) can occupy in the mempool regardless of replacement.
- Consider a stricter, separate TTL (or immediate removal policy) specifically for permanently gapped transactions, since they can never be included and thus should not indefinitely reserve pool capacity.

### Proof of Concept
1. Attacker submits `tx_hash: 1` from `address: 0xA` with `tx_nonce: N` where `account_nonce(0xA) = 0` and `N` is far above 0 (e.g., `N = 5`), and never submits the missing nonces `0..N-1`. This tx is accepted into the pool but is "gapped" and never queued for `get_txs` [7](#0-6) .
2. Just before `transaction_ttl` elapses, attacker submits a replacement `tx_hash: 2` at the same `(0xA, N)` with tip/gas price ≥ 10% higher than the current one, satisfying `should_replace_tx` [9](#0-8) . The replacement is inserted fresh, resetting `submission_time` [3](#0-2) .
3. Repeat step 2 indefinitely (or via multiple sybil addresses) until `capacity_in_bytes` is consumed.
4. Other users' `add_tx` calls now fail with `MempoolError::MempoolFull` (as demonstrated for a similar gapped-tx scenario in `fee_escalation_rejected_replacement_of_gapped_tx_keeps_existing_tx`) [6](#0-5) , denying network-wide transaction admission.

### Citations

**File:** crates/apollo_mempool_config/src/config.rs (L61-78)
```rust
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Validate)]
pub struct MempoolStaticConfig {
    pub enable_fee_escalation: bool,
    // Percentage increase for tip and max gas price to enable transaction replacement.
    #[validate(range(min = 1, max = 100))]
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
    // If true, only transactions with max L2 gas price per unit bound that are above the threshold
    // are inserted into the priority queue. If false, all transactions are inserted into the
    // priority queue.
    pub validate_resource_bounds: bool,
    // Time to wait before allowing a Declare transaction to be returned in `get_txs`.
    // Declare transactions are delayed to allow other nodes sufficient time to compile them.
    #[serde(deserialize_with = "deserialize_seconds_to_duration")]
    pub declare_delay: Duration,
    // Number of latest committed blocks for which committed account nonces are preserved.
    pub committed_nonce_retention_block_count: usize,
    // The maximum size of the mempool, in bytes.
    pub capacity_in_bytes: u64,
```

**File:** crates/apollo_mempool/src/mempool.rs (L410-443)
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
```

**File:** crates/apollo_mempool/src/mempool.rs (L756-819)
```rust
    /// Validates whether the incoming transaction may replace an existing one at the same
    /// `(address, nonce)` via fee escalation, without mutating any state. Returns the existing
    /// transaction to be replaced when a valid replacement exists, `None` when there is nothing to
    /// replace, or an error when a replacement is present but not permitted.
    fn validate_fee_escalation(
        &self,
        incoming_tx_reference: TransactionReference,
    ) -> MempoolResult<Option<TransactionReference>> {
        let TransactionReference { address, nonce, .. } = incoming_tx_reference;

        self.validate_no_delayed_declare_front_run(incoming_tx_reference)?;

        if !self.config.static_config.enable_fee_escalation {
            if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
                return Err(MempoolError::DuplicateNonce { address, nonce });
            };

            return Ok(None);
        }

        let Some(existing_tx_reference) = self.tx_pool.get_by_address_and_nonce(address, nonce)
        else {
            // Replacement irrelevant: no existing transaction with the same nonce for address.
            return Ok(None);
        };

        if !self.should_replace_tx(&existing_tx_reference, &incoming_tx_reference) {
            info!(
                "{existing_tx_reference} was not replaced by {incoming_tx_reference} due to \
                 insufficient fee escalation."
            );
            // TODO(Elin): consider adding a more specific error type / message.
            return Err(MempoolError::DuplicateNonce { address, nonce });
        }

        Ok(Some(existing_tx_reference))
    }

    /// Removes the existing transaction that is being replaced via fee escalation. Must be called
    /// only after the incoming replacement is guaranteed to be admitted, so the account is never
    /// left without a transaction at this nonce.
    fn remove_replaced_tx(&mut self, existing_tx_reference: TransactionReference) {
        debug!("{existing_tx_reference} is being replaced via fee escalation.");

        self.tx_queue.remove_txs(&[existing_tx_reference]);
        self.tx_pool
            .remove(existing_tx_reference.tx_hash)
            .expect("Transaction hash from pool must exist.");
        self.decrement_stuck_txs_if_gap_account(existing_tx_reference.address, 1);
    }

    fn should_replace_tx(
        &self,
        existing_tx: &TransactionReference,
        incoming_tx: &TransactionReference,
    ) -> bool {
        let [existing_tip, incoming_tip] =
            [existing_tx, incoming_tx].map(|tx| u128::from(tx.tip.0));
        let [existing_max_l2_gas_price, incoming_max_l2_gas_price] =
            [existing_tx, incoming_tx].map(|tx| tx.max_l2_gas_price.0);

        self.increased_enough(existing_tip, incoming_tip)
            && self.increased_enough(existing_max_l2_gas_price, incoming_max_l2_gas_price)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L849-853)
```rust
    fn remove_expired_txs(&mut self) -> AddressToNonce {
        let removed_txs = self
            .tx_pool
            .remove_txs_older_than(self.config.dynamic_config.transaction_ttl, &self.state.staged);

```

**File:** crates/apollo_mempool/src/mempool.rs (L947-960)
```rust
    fn update_accounts_with_gap(&mut self, address_to_nonce: AddressToNonce) {
        for (address, account_nonce) in address_to_nonce {
            // If a delayed declare transaction exists at the account nonce, it is next to execute,
            // so no gap exists.
            if self.delayed_declares.contains(address, account_nonce) {
                self.remove_from_accounts_with_gap(address);
                continue;
            }

            // Gap exists when lowest transaction nonce is higher than account nonce.
            let gap_exists = match self.tx_pool.get_lowest_nonce(address) {
                Some(lowest_nonce) => account_nonce < lowest_nonce,
                None => false, // No transactions for the account, so no gap.
            };
```

**File:** crates/apollo_mempool/src/mempool.rs (L2019-2035)
```rust

```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L414-421)
```rust
    fn insert(&mut self, tx: TransactionReference) -> Option<SubmissionID> {
        let submission_id = SubmissionID {
            submission_time: self.clock.now(),
            tx_hash: tx.tx_hash,
            batching_time: None,
        };
        self.txs_by_submission_time.insert(submission_id.clone(), tx);
        self.hash_to_submission_id.insert(tx.tx_hash, submission_id)
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L2019-2035)
```rust
#[rstest]
fn fee_escalation_rejected_replacement_of_gapped_tx_keeps_existing_tx() {
    // Existing tx A sits at a future nonce (5) while the account nonce is 0, so A is gapped. A
    // gapped account is never granted eviction, so a larger replacement is rejected.
    let existing_tx = invoke_tx_with_signature_size(1, "0x0", 5, 90, 90, 0);
    let mut mempool = full_mempool_with_fee_escalation(existing_tx.total_bytes());
    add_tx(&mut mempool, &add_tx_input_for(existing_tx.clone(), "0x0", 0));

    let larger_replacement =
        add_tx_input_for(invoke_tx_with_signature_size(2, "0x0", 5, 100, 100, 32), "0x0", 0);
    add_tx_expect_error(&mut mempool, &larger_replacement, MempoolError::MempoolFull);

    assert!(
        mempool.tx_pool.get_by_tx_hash(existing_tx.tx_hash()).is_ok(),
        "rejected replacement of a gapped tx must not drop it"
    );
}
```
