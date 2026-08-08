## Title
`BankHashStats` accumulates every intra-batch account write instead of only the final committed state, letting same-slot transient balance/state changes permanently pollute persisted, snapshot-serialized statistics — ([File: runtime/src/bank.rs])

### Summary
The Aragon report's root cause is a security/economic check that reads a *transient* balance which can be inflated and reverted atomically within a single unit of execution, while the effect of that transient read is nonetheless durably recorded. The closest reachable analog in this codebase is in `Bank::update_bank_hash_stats`, called from `commit_transactions()`/`store_accounts()`. Unlike the accounts write cache and the accounts-lt-hash enqueue path — both of which explicitly deduplicate multiple writes to the same pubkey within one batch and keep only the *last* version — `update_bank_hash_stats` iterates over every entry in the batch and unconditionally calls `stats.update(&account)` for each one, with no de-duplication.

### Finding Description
`write_accounts_to_cache` explicitly walks the batch in reverse and skips all but the newest write per pubkey, with an explicit comment: "Ordering of accounts is important as duplicate pubkeys are possible. The last account ... for each pubkey is stored in the write cache." [1](#0-0) 

`enqueue_on_chain_accounts_lt_hash_updates` similarly dedups: "process accounts in reverse because we must only count the latest version of each account", using a `seen_accounts` set to skip all but the newest write per pubkey before mixing it into the accounts lattice hash. [2](#0-1) 

By contrast, `update_bank_hash_stats` performs no such de-duplication — it walks every index in the incoming `StorableAccounts` batch and calls `stats.update(&account)` unconditionally for each one, then accumulates the result into the bank-wide `bank_hash_stats`: [3](#0-2) 

This is invoked once per `commit_transactions()` call, i.e., once per batch of sanitized transactions collected into a single PoH entry within one slot: [4](#0-3) 

Because a single batch can legitimately contain multiple transactions that write to the *same* pubkey (e.g., one transaction inflates an account's lamports/data and a later transaction in the same batch reverts it, functionally the "flashloan" pattern of the external report — an atomic borrow-and-repay within one indivisible unit of execution/one slot), `update_bank_hash_stats` counts every intermediate write towards the account/lamport/data statistics, even though only the final state is ever actually persisted to the write cache and index (per `write_accounts_to_cache`'s explicit last-writer-wins semantics). The transient, reverted intermediate state therefore leaves a permanent footprint in `BankHashStats` that does not correspond to any value that ever exists after the batch commits.

This is consequential because `BankHashStats` is not a purely ephemeral metric — it is captured into the snapshot's bank fields and round-tripped through snapshot serialization/deserialization: [5](#0-4) 

### Impact Explanation
`BankHashStats` is surfaced in `hash_internal_state()`'s log line (`stats: {bank_hash_stats:?}`) and is persisted/round-tripped through snapshots via `bank_fields.bank_hash_stats`, but based on the code inspected here it is not mixed into the actual consensus bank hash bytes (only `parent_hash`, `signature_count`, `last_blockhash`, and the accounts lt hash are hashed in `hash_internal_state`): [6](#0-5) 

Because I could not find code in this codebase that uses `BankHashStats` for any consensus-affecting decision (unlike `capitalization`, which is explicitly checked against `calculate_capitalization_for_tests()` and fails snapshot loading on mismatch), the impact of this over-counting appears to be limited to diagnostic/statistics drift rather than a consensus-breaking bank-hash mismatch, an honest-node snapshot-vs-replay divergence, a silent balance change, or a panic. Given the "no-impact analog" exclusion in scope, this finding is weak: it demonstrates the exact bug *pattern* from the external report (a transient, reverted intra-batch state is counted as if it persisted) reachable through unprivileged, ordinary same-slot multi-transaction activity, but I was not able to confirm any concrete downstream consequence (incorrect capitalization, incorrect hash, or node panic) resulting from the stats drift.

### Likelihood Explanation
The trigger conditions are trivially reachable by any unprivileged user: submit two or more transactions in the same block that write to the same account such that the net effect cancels out (e.g., transfer lamports in then out, or write data then revert it) within transactions that land in the same `commit_transactions()` batch. No validator/operator privilege is required, and this does not depend on a mocked-only or theoretical-only scenario — `commit_transactions()` is the normal execution path for every batch of transactions in banking stage / replay.

### Recommendation
Apply the same "last write per pubkey wins" de-duplication in `update_bank_hash_stats` that is already used in `write_accounts_to_cache` and `enqueue_on_chain_accounts_lt_hash_updates`, so that `BankHashStats` only reflects state that is actually persisted after a batch commits, rather than accumulating every intermediate, potentially reverted write within the batch. Additionally, audit remaining consumers/serializers of `BankHashStats` to confirm whether any consensus-relevant or snapshot-validation logic depends on its exact values, and if so, treat this as higher severity.

### Proof of Concept
1. Construct a single PoH entry/batch containing at least two transactions in the same slot that both write to the same pubkey `P`, e.g.:
   - Tx1: increase `P`'s lamports/data size significantly (e.g., simulate a large transient balance).
   - Tx2 (same batch): revert `P` back to its original lamports/data size.
2. Call `Bank::commit_transactions()` with both transactions in the same batch (this is exactly the input shape `collect_accounts_to_store` produces for `update_bank_hash_stats` and `enqueue_on_chain_accounts_lt_hash_updates`), per: [7](#0-6) 
3. Observe that `write_accounts_to_cache` only ever stores/counts the final (reverted) state of `P` (via its explicit last-write-wins dedup), while `update_bank_hash_stats` calls `stats.update()` twice — once for the inflated intermediate state and once for the reverted final state — even though the inflated state is never actually written to the cache/index and never exists as a loadable account state.
4. Compare the bank's `bank_hash_stats` counters (e.g., `num_updated_accounts`, lamport/data-size aggregates) against what would be expected if only the final, persisted state of `P` were counted; they will diverge, since the transient intermediate write is counted despite never being committed to storage.

*Note: I was unable to fully confirm whether `BankHashStats` divergence has any consensus or snapshot-validation consequence within the code I could inspect (unlike `capitalization`, which is explicitly checked on snapshot load). This uncertainty should be resolved before treating this finding as high severity; if `BankHashStats` is purely informational, this finding may not meet the "concrete impact" bar required by the validation rules.*

### Citations

**File:** accounts-db/src/accounts_db.rs (L5408-5436)
```rust
    // Stores accounts in the write cache. If an account is zero-lamport and not present in the
    // cache or index, there is no need to store it in the write cache as it will not affect the
    // accounts hash. The function returns a BitVec indicating whether each account was stored in
    // the cache. Ordering of accounts is important as duplicate pubkeys are possible. The last
    // account in accounts_and_meta_to_store for each pubkey is stored in the write cache.
    fn write_accounts_to_cache<'a, 'b>(
        &self,
        slot: Slot,
        accounts_and_meta_to_store: &impl StorableAccounts<'b>,
        ancestors: &Ancestors,
    ) -> (BitVec, WriteAccountsToCacheStats) {
        let len = accounts_and_meta_to_store.len();
        let mut pubkey_set = HashSet::with_capacity_and_hasher(len, PubkeyHasherBuilder::default());
        let mut stats = WriteAccountsToCacheStats {
            num_initial_accounts_to_store: len as u64,
            ..Default::default()
        };
        let mut store_account = BitVec::new_fill(false, len as u64);

        (0..len).rev().for_each(|index| {
            accounts_and_meta_to_store.account_default_if_zero_lamport(index, |account| {
                let pubkey = account.pubkey();
                let is_duplicate_account = !pubkey_set.insert(*pubkey);
                if is_duplicate_account {
                    // If the same account is written multiple times in the same batch,
                    // only store the latest version
                    stats.num_duplicate_accounts_skipped += 1;
                    return;
                }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L29-57)
```rust
impl Bank {
    /// Enqueues the accounts lt hash updates for `accounts` to the accounts hasher thread pool.
    ///
    /// This fn is meant to be called by on-chain events, e.g. transaction processing.
    /// This fn deduplicates from `accounts`, keeping only the latest version of each account.
    /// It also loads the previous version of each account inline, because we assume the previous
    /// version of each account is still in the accounts write cache, and thus fast to load.
    ///
    /// For non-transaction processing callers, consider `enqueue_off_chain_accounts_lt_hash_updates()`.
    pub fn enqueue_on_chain_accounts_lt_hash_updates<'a>(
        &self,
        accounts: &impl StorableAccounts<'a>,
    ) {
        if accounts.is_empty() {
            return;
        }

        let seen_accounts_freelist = seen_accounts_freelist();
        let mut seen_accounts = seen_accounts_freelist.try_pop().unwrap_or_default();
        let async_progress = &self.accounts_lt_hash_async_progress;
        let thread_pool = accounts_hasher_thread_pool();

        // process accounts in reverse because we must only count the latest version of each account
        for index in (0..accounts.len()).rev() {
            let address = accounts.pubkey(index);
            if !seen_accounts.insert(*address) {
                // we've already enqueued a newer update for the same account; skip this one
                continue;
            }
```

**File:** runtime/src/bank.rs (L4307-4315)
```rust
    fn update_bank_hash_stats<'a>(&self, accounts: &impl StorableAccounts<'a>) {
        let mut stats = BankHashStats::default();
        (0..accounts.len()).for_each(|i| {
            accounts.account(i, |account| {
                stats.update(&account);
            })
        });
        self.bank_hash_stats.accumulate(&stats);
    }
```

**File:** runtime/src/bank.rs (L4355-4378)
```rust
        let ((), store_accounts_us) = measure_us!({
            // If geyser is present, we must collect `SanitizedTransaction`
            // references in order to comply with that interface - until it
            // is changed.
            let maybe_transaction_refs = self
                .accounts()
                .accounts_db
                .has_accounts_update_notifier()
                .then(|| {
                    sanitized_txs
                        .iter()
                        .map(|tx| tx.as_sanitized_transaction())
                        .collect::<Vec<_>>()
                });

            let (accounts_to_store, transactions) = collect_accounts_to_store(
                sanitized_txs,
                &maybe_transaction_refs,
                &processing_results,
            );

            let to_store = (self.slot(), accounts_to_store.as_slice());
            self.update_bank_hash_stats(&to_store);
            self.enqueue_on_chain_accounts_lt_hash_updates(&to_store);
```

**File:** runtime/src/bank.rs (L5345-5413)
```rust
    fn hash_internal_state(&self) -> Hash {
        let measure_total = Measure::start("");
        let slot = self.slot();

        let mut hash = hashv(&[
            self.parent_hash.as_ref(),
            &self.signature_count().to_le_bytes(),
            self.last_blockhash().as_ref(),
        ]);

        let accounts_lt_hash_checksum = {
            let accounts_lt_hash = &*self.accounts_lt_hash.lock().unwrap();
            let lt_hash_bytes = bytemuck::must_cast_slice(&accounts_lt_hash.0.0);
            hash = hashv(&[hash.as_ref(), lt_hash_bytes]);
            accounts_lt_hash.0.checksum()
        };

        let buf = self
            .hard_forks
            .read()
            .unwrap()
            .get_hash_data(slot, self.parent_slot());
        if let Some(buf) = buf {
            let hard_forked_hash = hashv(&[hash.as_ref(), &buf]);
            warn!("hard fork at slot {slot} by hashing {buf:?}: {hash} => {hard_forked_hash}");
            hash = hard_forked_hash;
        }

        #[cfg(feature = "dev-context-only-utils")]
        let hash_override = self
            .hash_overrides
            .lock()
            .unwrap()
            .get_bank_hash_override(slot)
            .copied()
            .inspect(|&hash_override| {
                if hash_override != hash {
                    info!(
                        "bank: slot: {}: overrode bank hash: {} with {}",
                        self.slot(),
                        hash,
                        hash_override
                    );
                }
            });
        // Avoid to optimize out `hash` along with the whole computation by super smart rustc.
        // hash_override is used by ledger-tool's simulate-block-production, which prefers
        // the actual bank freezing processing for accurate simulation.
        #[cfg(feature = "dev-context-only-utils")]
        let hash = hash_override.unwrap_or(std::hint::black_box(hash));

        let bank_hash_stats = self.bank_hash_stats.load();

        let total_us = measure_total.end_as_us();

        datapoint_info!(
            "bank-hash_internal_state",
            ("slot", slot, i64),
            ("total_us", total_us, i64),
        );
        info!(
            "bank frozen: {slot} hash: {hash} signature_count: {} last_blockhash: {} \
             capitalization: {}, accounts_lt_hash checksum: {accounts_lt_hash_checksum}, stats: \
             {bank_hash_stats:?}",
            self.signature_count(),
            self.last_blockhash(),
            self.capitalization(),
        );
        hash
```

**File:** runtime/src/serde_snapshot.rs (L961-994)
```rust
    let (accounts_db, reconstructed_accounts_db_info) = reconstruct_accountsdb_from_fields(
        snapshot_accounts_db_fields,
        account_paths,
        storage_and_next_append_vec_id,
        limit_load_slot_count_from_snapshot,
        verify_index,
        accounts_db_config,
        accounts_update_notifier,
        exit,
    )?;
    bank_fields.bank_hash_stats = reconstructed_accounts_db_info.bank_hash_stats;

    let bank_rc = BankRc::new(Accounts::new(Arc::new(accounts_db)));
    let runtime_config = Arc::new(runtime_config.clone());
    let epoch_stakes = epoch_stakes_handle.join().expect("calculate epoch stakes");

    let bank = Bank::new_from_snapshot(
        bank_rc,
        genesis_config,
        runtime_config,
        bank_fields,
        leader_for_tests,
        debug_keys,
        reconstructed_accounts_db_info.accounts_data_len,
        epoch_stakes,
    );

    Ok((
        bank,
        ReconstructedBankInfo {
            calculated_accounts_lt_hash: reconstructed_accounts_db_info.calculated_accounts_lt_hash,
            calculated_capitalization: reconstructed_accounts_db_info.calculated_capitalization,
        },
    ))
```
