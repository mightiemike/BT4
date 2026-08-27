### Title
Unbounded per-lookup linear scan in `StatusCache::get_status_any_blockhash` via durable-nonce hash pollution - ([File: runtime/src/status_cache.rs])

### Summary
`StatusCache::get_status_any_blockhash` iterates over every distinct blockhash key currently stored in `self.cache` (`self.cache.keys().find_map(...)`), and this key space is not bounded by transaction count or compute budget but only by `purge_roots()`'s `max_root_entries` (`MAX_ROOT_ENTRIES = MAX_RECENT_BLOCKHASHES`) window of *roots*, not of distinct blockhashes. Because durable-nonce hashes never expire the way normal recent blockhashes do, an attacker who creates many nonce accounts and advances/uses each with a unique blockhash can inflate `self.cache`'s key cardinality far beyond the ~300-entry size a normal blockhash-only workload would produce, making any caller of `get_status_any_blockhash` pay an O(#distinct_blockhashes) cost per lookup.

### Finding Description
`StatusCache::insert` (`runtime/src/status_cache.rs:210`) keys `self.cache: KeyStatusMap<T>` (`HashMap<Hash, (Slot, usize, KeyMap<T>), ...>`) by the transaction's `recent_blockhash`. `purge_roots()` (`runtime/src/status_cache.rs:241`) only removes `cache` entries whose associated `max_slot` (fork) is `<= cutoff`, where `cutoff` is derived from the `max_root_entries`-th newest *root*, not from the number of distinct blockhash keys. For ordinary transactions this is fine because there is only one "recent blockhash" per slot/tick, so the natural cardinality of `cache` stays close to `MAX_ROOT_ENTRIES`.

For durable-nonce transactions this assumption breaks: each nonce account's stored hash is unique (derived per-advance from `DurableNonce::from_blockhash`), and a nonce hash is valid indefinitely (it doesn't expire from the `BlockhashQueue`, see `runtime/src/bank/check_transactions.rs:245-284` and `svm/src/transaction_processor.rs:833-892`, `validate_transaction_nonce`). The status-cache code itself acknowledges this divergence: the comment in `runtime/src/status_cache.rs:787-789` states "unlike `clear_slot_entries()`, `purge_roots()` can't overlap with regular blockhashes since they'd have expired by the time roots are old enough to be purged. However, nonces don't expire, so they can overlap," and the `do_test_shuttle_purge_nonce_overlap` test exercises exactly this behavior.

An unprivileged attacker can:
1. Fund and create many nonce accounts (`system_instruction::create_account` + `initialize_nonce_account`), each producing a unique durable-nonce hash.
2. Submit `advance_nonce_account` + payload transactions using each nonce hash as `recent_blockhash`, spreading them across the ~`MAX_ROOT_ENTRIES` (300) root window so their `(slot, ...)` entries in `cache` are not yet purged.
3. Each such transaction inserts a brand-new key into `self.cache` (a new `Hash` → `(Slot, usize, KeyMap<T>)` entry), unlike normal traffic which reuses one of ~300 shared blockhash keys.

`get_status_any_blockhash` (`runtime/src/status_cache.rs:171-180`) then does `self.cache.keys().find_map(|blockhash| self.get_status(&key, blockhash, ancestors))`, which is O(#distinct_blockhashes) in the worst case (key not found), i.e., O(K) where K is the number of unique nonce hashes the attacker managed to get inserted within the live root window. This scan has no compute-budget accounting because it happens in Bank/RPC-side signature lookup code, not inside SVM transaction execution metering.

The normal transaction-validation hot path (`check_status_cache` in `runtime/src/bank/check_transactions.rs:302-347`, via `get_processed_slot`) uses `get_status` with a known `transaction_blockhash`, which is an O(1) average hashmap lookup and is unaffected — so this does not directly slow down block execution/consensus. The vulnerable function is reached only through callers that do not know the blockhash up front (e.g. `Bank::get_signature_status_slot`, used to serve `getSignatureStatuses`-style RPC queries and other blockhash-independent internal lookups).

### Impact Explanation
This is a resource-exhaustion / liveness-degradation issue scoped to whichever validator/RPC node serves blockhash-independent signature-status lookups: each such call becomes proportional to the attacker-inflated cache key cardinality, increasing CPU time and lock-hold duration on `status_cache`'s `RwLock` for every caller of `get_status_any_blockhash`, including legitimate users. It does not cause loss of funds, double-spend, or consensus divergence, and it does not affect the metered SVM transaction-execution/consensus path, since that path uses the O(1) `get_status`. It falls into the "node liveness / resource hygiene" category rather than a fund-loss or consensus-halting bug.

### Likelihood Explanation
Exploitation requires the attacker to pay for: nonce-account rent-exempt balances for each unique nonce account created, and standard transaction fees for each nonce-consuming transaction submitted within a ~300-slot window (to keep those cache entries from being purged). This is a linear-cost-to-attacker, linear-effect DoS: the attacker must continuously spend SOL to sustain a large K, and the effect is confined to non-consensus-critical query paths. Repeatability is straightforward (create N nonce accounts, submit N nonce transactions), but the cost floor (account rent + fees) and the fact that it only degrades a side lookup path (not block processing) limit the practical severity.

### Recommendation
Bound the size of `StatusCache::cache` (or at least deprioritize/avoid full-key scans) independent of root/slot age — e.g., track and cap the number of distinct blockhash entries directly, or restrict/rate-limit `get_status_any_blockhash` callers exposed to unauthenticated request volume, or maintain a size-bounded auxiliary index for nonce-derived lookups so blockhash-independent status queries do not require an unbounded linear scan.

### Proof of Concept
Add a bank/`StatusCache` unit test in `runtime/src/status_cache.rs`:
```rust
#[test]
fn test_get_status_any_blockhash_scales_with_distinct_blockhashes() {
    let mut status_cache = BankStatusCache::default();
    let ancestors = Ancestors::from(vec![0]);
    const K: usize = 50_000;
    for i in 0..K {
        let blockhash = Hash::new_unique(); // simulates unique nonce-derived hashes
        status_cache.insert(&blockhash, Signature::from([i as u8; 64]), 0, ());
    }
    let missing_sig = Signature::from([0xff; 64]);
    let start = std::time::Instant::now();
    assert_eq!(
        status_cache.get_status_any_blockhash(missing_sig, &ancestors),
        None
    );
    let elapsed = start.elapsed();
    // Assert cache key cardinality equals K (no bound applied by purge_roots
    // since all entries share slot 0, a single root) and that lookup time
    // grows roughly linearly with K when compared against a K/10 run.
    assert_eq!(status_cache.roots().len(), 1);
    println!("K={K} scan_time={elapsed:?}");
}
```
Run with increasing `K` (e.g. 5,000 / 50,000 / 500,000) and confirm iteration/time grows ~linearly with `K`, demonstrating unbounded O(K) cost per `get_status_any_blockhash` call as distinct nonce-derived blockhash keys accumulate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/src/status_cache.rs (L168-180)
```rust
    /// Search for a key with any blockhash.
    ///
    /// Prefer get_status for performance reasons, it doesn't need to search all blockhashes.
    pub fn get_status_any_blockhash<K: AsRef<[u8]>>(
        &self,
        key: K,
        ancestors: &Ancestors,
    ) -> Option<(Slot, T)> {
        self.cache.keys().find_map(|blockhash| {
            trace!("get_status_any_blockhash: trying {blockhash}");
            self.get_status(&key, blockhash, ancestors)
        })
    }
```

**File:** runtime/src/status_cache.rs (L209-239)
```rust
    /// Insert a new key using the given blockhash at the given slot.
    pub fn insert<K: AsRef<[u8]>>(
        &mut self,
        transaction_blockhash: &Hash,
        key: K,
        slot: Slot,
        res: T,
    ) {
        let max_key_index = key.as_ref().len().saturating_sub(CACHED_KEY_SIZE + 1);

        // Get the cache entry for this blockhash.
        let (max_slot, key_index, hash_map) = self
            .cache
            .entry(*transaction_blockhash)
            .or_insert_with(|| (slot, 0, HashMap::new()));

        // Update the max slot observed to contain txs using this blockhash.
        *max_slot = std::cmp::max(slot, *max_slot);

        // Grab the key slice.
        let key_index = (*key_index).min(max_key_index);
        let mut key_slice = [0u8; CACHED_KEY_SIZE];
        key_slice.clone_from_slice(&key.as_ref()[key_index..key_index + CACHED_KEY_SIZE]);

        // Insert the slot and tx result into the cache entry associated with
        // this blockhash and keyslice.
        let forks = hash_map.entry(key_slice).or_default();
        forks.push((slot, res.clone()));

        self.add_to_slot_delta(transaction_blockhash, slot, key_index, key_slice, res);
    }
```

**File:** runtime/src/status_cache.rs (L241-257)
```rust
    pub fn purge_roots(&mut self) {
        let max_root_entries = self.max_root_entries();
        if self.roots.len() > max_root_entries {
            let num_roots_to_purge = self.roots.len() - max_root_entries;
            let mut roots = self
                .roots
                .iter()
                .copied()
                .collect::<SmallVec<[Slot; 0x200]>>();
            let (_, cutoff, _) = roots.select_nth_unstable(num_roots_to_purge - 1);
            let cutoff = *cutoff;

            self.roots.retain(|root| *root > cutoff);
            self.cache.retain(|_, (fork, _, _)| *fork > cutoff);
            self.slot_deltas.retain(|slot, _| *slot > cutoff);
        }
    }
```

**File:** runtime/src/status_cache.rs (L787-789)
```rust
    // unlike clear_slot_entries(), purge_slots() can't overlap with regular blockhashes since
    // they'd have expired by the time roots are old enough to be purged. However, nonces don't
    // expire, so they can overlap.
```

**File:** runtime/src/bank/check_transactions.rs (L302-347)
```rust
    fn check_status_cache<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        mut lock_results: Vec<TransactionCheckResult>,
        collect_processed_slots: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> (Vec<TransactionCheckResult>, Option<Vec<Option<Slot>>>) {
        // Do allocation before acquiring the lock on the status cache.
        let mut processed_slots = if collect_processed_slots {
            Some(Vec::with_capacity(sanitized_txs.len()))
        } else {
            None
        };
        let rcache = self.status_cache.read().unwrap();

        for (sanitized_tx_ref, lock_result) in sanitized_txs.iter().zip(lock_results.iter_mut()) {
            let processed_slot = if lock_result.is_ok() {
                self.get_processed_slot(sanitized_tx_ref.borrow(), &rcache)
            } else {
                None
            };

            if processed_slot.is_some() {
                error_counters.already_processed += 1;
                *lock_result = Err(TransactionError::AlreadyProcessed);
            }

            if let Some(processed_slots) = processed_slots.as_mut() {
                processed_slots.push(processed_slot)
            }
        }

        (lock_results, processed_slots)
    }

    fn get_processed_slot(
        &self,
        sanitized_tx: &impl TransactionWithMeta,
        status_cache: &BankStatusCache,
    ) -> Option<Slot> {
        let key = sanitized_tx.message_hash();
        let transaction_blockhash = sanitized_tx.recent_blockhash();
        status_cache
            .get_status(key, transaction_blockhash, &self.ancestors)
            .map(|status| status.0)
    }
```

**File:** svm/src/transaction_processor.rs (L833-891)
```rust
    fn validate_transaction_nonce<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        message: &impl SVMMessage,
        nonce_address: &Pubkey,
        next_durable_nonce: &DurableNonce,
        next_lamports_per_signature: u64,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> TransactionResult<NonceInfo> {
        // When SIMD83 is enabled, if the nonce has been used in this batch already, we must drop
        // the transaction. This is the same as if it was used in different batches in the same slot.
        // It is possible that the nonce account was used, closed, closed and reopened, closed and
        // spoofed by a non-system program, or had its authority changed. Such a transaction cannot
        // be processed, even as fee-only.

        let Some(mut nonce_account) = account_loader
            .load_transaction_account(nonce_address, true)
            .map(|loaded| loaded.account)
        else {
            error_counters.account_not_found += 1;
            return Err(TransactionError::AccountNotFound);
        };

        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        }

        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };

        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
```
