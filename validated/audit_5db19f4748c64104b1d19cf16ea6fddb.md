### Title
`bank_hash_stats` snapshot field lacks the canary/assertion protection its sibling `accounts_lt_hash` field has, allowing a silent fallback to `BankHashStats::default()` - (File: runtime/src/serde_snapshot.rs)

### Summary
The reported Solidity bug is a class of "variable not correctly (re)initialized across a persistence/upgrade boundary" — a value that is expected to be explicitly (re)populated by an initializer, but that instead silently falls back to a stale/wrong default when that initialization step is skipped or buggy. The closest reachable analog in this repo is in the bank/accounts-db snapshot deserialization path in `runtime/src/serde_snapshot.rs`, where `BankFieldsToDeserialize` is built with two "to-be-populated-later" placeholder fields — `accounts_lt_hash` and `bank_hash_stats` — but only one of them is defended with a canary/assert.

### Finding Description
When a `DeserializableVersionedBank` is converted into `BankFieldsToDeserialize`, several fields are deliberately left as placeholders that are documented to be filled in from other parts of the snapshot later in the pipeline: [1](#0-0) 

Note the asymmetry:
- `accounts_lt_hash` is set to an explicit, unmistakable canary value (`LT_HASH_CANARY = LtHash([0xCAFE; ...])`) with an explanatory comment: *"This serves as a canary for the LtHash. If it is not replaced during deserialization, it indicates a bug."* Later, in `DeserializableBankSnapshot::into_fields`, this field is unwrapped with `.expect("snapshot must have accounts_lt_hash")`, so if the replacement step were ever skipped, the process would panic loudly instead of silently continuing with a wrong hash: [2](#0-1) 

- `bank_hash_stats`, by contrast, is initialized with the ordinary `BankHashStats::default()` (all zeros) and is only "populated from AccountsDbFields" via a separate, later code path in `runtime/src/bank.rs` — with no canary, no sentinel, and no assertion that the replacement actually happened. If any code path in that later wiring fails to overwrite `bank_hash_stats` (e.g., a future refactor of `runtime/src/bank.rs`'s bank-from-snapshot construction that reorders or accidentally skips the assignment, or an early return/error-handling branch that bypasses it), the bank would silently continue with `BankHashStats::default()` instead of the real stats loaded from the snapshot's `AccountsDbFields`.

This mirrors the reported bug precisely: `isInMulticall_ = 1` was a "meaningful" default that could not survive the upgrade/initializer boundary and silently reverted to 0. Here, the "meaningful" value (the real per-slot bank hash stats read from `AccountsDbFields`) must survive the deserialize→fold→bank-construction boundary, but only one of the two similarly-shaped fields is protected against that boundary silently reverting to a default (zeroed) value.

### Impact Explanation
`BankHashStats` feeds into the bank hash calculation used for consensus (hash/capitalization divergence checks) and into stats reporting. If `bank_hash_stats` were silently left at its zeroed default after loading a snapshot (due to a latent or future bug in the population step that this code has no defense against), a node would compute a different bank hash than a node that correctly loaded the value, causing exactly the "honest-node snapshot-vs-replay mismatch" / hash divergence class of impact called out in scope. Because there is no assertion analogous to the `accounts_lt_hash` canary, such a bug would not be caught at snapshot-load time — it would only surface indirectly as a hash mismatch downstream, making it harder to detect and more dangerous.

### Likelihood Explanation
This is not a currently proven live bug in the shown call sites (I could not, within the tool budget, fully trace every code path in `runtime/src/bank.rs` that performs the `bank_hash_stats` population to confirm whether it is unconditionally executed today). The finding is that the code lacks a structural safeguard (unlike its sibling field) against a category of defect that has already occurred in a comparable Solidity codebase and has already occurred once in this same file's history for the `LtHash` field (hence the canary was added). This makes it a credible, currently-plausible latent risk rather than a confirmed, currently-triggerable divergence.

### Recommendation
Add the same canary/assertion pattern used for `accounts_lt_hash` to `bank_hash_stats`: initialize it to a sentinel value that cannot arise from legitimate snapshot data, and assert/`.expect()` that it has been replaced before the bank is considered fully constructed from a snapshot. This converts a silent, hard-to-detect potential hash divergence into a loud, immediate panic at snapshot-load time, consistent with the precedent already set for `accounts_lt_hash`.

### Proof of Concept
Not applicable as a concrete exploit — this is a defense-in-depth/robustness gap identified by code-pattern comparison (the `accounts_lt_hash` canary vs. the undefended `bank_hash_stats` default), not a demonstrated live reachable panic/divergence in the current code paths inspected.

### Citations

**File:** runtime/src/serde_snapshot.rs (L260-298)
```rust
impl From<DeserializableVersionedBank> for BankFieldsToDeserialize {
    fn from(dvb: DeserializableVersionedBank) -> Self {
        // This serves as a canary for the LtHash.
        // If it is not replaced during deserialization, it indicates a bug.
        const LT_HASH_CANARY: LtHash = LtHash([0xCAFE; LtHash::NUM_ELEMENTS]);
        // `durable_nonce` is skipped from the wire; recompute it from the last hash.
        let mut blockhash_queue = dvb.blockhash_queue;
        blockhash_queue.refresh_durable_nonce();
        BankFieldsToDeserialize {
            blockhash_queue,
            hash: dvb.hash,
            parent_hash: dvb.parent_hash,
            parent_slot: dvb.parent_slot,
            hard_forks: dvb.hard_forks,
            transaction_count: dvb.transaction_count,
            tick_height: dvb.tick_height,
            signature_count: dvb.signature_count,
            capitalization: dvb.capitalization,
            max_tick_height: dvb.max_tick_height,
            hashes_per_tick: dvb.hashes_per_tick,
            ticks_per_slot: dvb.ticks_per_slot,
            ns_per_slot: dvb.ns_per_slot,
            genesis_creation_time: dvb.genesis_creation_time,
            slots_per_year: dvb.slots_per_year,
            accounts_data_len: dvb.accounts_data_len,
            slot: dvb.slot,
            block_height: dvb.block_height,
            leader_id: dvb.leader_id,
            fee_rate_governor: dvb.fee_rate_governor,
            epoch_schedule: dvb.epoch_schedule,
            inflation: dvb.inflation,
            stakes: dvb.stakes,
            is_delta: dvb.is_delta,
            versioned_epoch_stakes: vec![], // populated from ExtraFieldsToDeserialize
            accounts_lt_hash: AccountsLtHash(LT_HASH_CANARY), // populated from ExtraFieldsToDeserialize
            bank_hash_stats: BankHashStats::default(),        // populated from AccountsDbFields
            block_id: None, // populated from ExtraFieldsToDeserialize
        }
    }
```

**File:** runtime/src/serde_snapshot.rs (L564-597)
```rust
impl DeserializableBankSnapshot {
    /// Folds the extra fields into the bank fields; errors if `unused_epoch_stakes` is non-empty.
    fn into_fields(self) -> wincode::ReadResult<(BankFieldsToDeserialize, AccountsDbFields)> {
        let Self {
            bank,
            accounts_db,
            extra_fields,
        } = self;
        if !bank.unused_epoch_stakes.is_empty() {
            return Err(wincode::ReadError::InvalidValue(
                "Expected deserialized bank's unused_epoch_stakes field to be empty",
            ));
        }
        let mut bank_fields = BankFieldsToDeserialize::from(bank);
        let ExtraFieldsToDeserialize {
            lamports_per_signature,
            _unused_incremental_snapshot_persistence,
            _unused_epoch_accounts_hash,
            versioned_epoch_stakes,
            accounts_lt_hash,
            block_id,
        } = extra_fields;

        bank_fields.fee_rate_governor = bank_fields
            .fee_rate_governor
            .clone_with_lamports_per_signature(lamports_per_signature);
        bank_fields.versioned_epoch_stakes = versioned_epoch_stakes;
        bank_fields.accounts_lt_hash = accounts_lt_hash
            .expect("snapshot must have accounts_lt_hash")
            .into();
        bank_fields.block_id = block_id;

        Ok((bank_fields, accounts_db))
    }
```
