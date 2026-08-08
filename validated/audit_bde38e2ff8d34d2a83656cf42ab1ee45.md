## Title
Snapshot deserialization panics via `.expect()` on `accounts_lt_hash`, a field that can legitimately be absent - (`runtime/src/serde_snapshot.rs`)

## Summary
`DeserializableBankSnapshot::into_fields()` unconditionally unwraps the optional `accounts_lt_hash` extra field with `.expect("snapshot must have accounts_lt_hash")`. The field is deserialized with a `default_on_eof`/`DefaultOnEmptyRead` strategy that yields `None` when the bytes are missing from the snapshot stream, meaning the deserializer explicitly anticipates that this field may not be present — yet the very next step treats its absence as an unrecoverable error and panics the process, rather than returning a `Result` error that can be handled gracefully.

## Finding Description
The `ExtraFieldsToDeserialize` struct marks `accounts_lt_hash` as `Option<SerdeAccountsLtHash>`, using `#[serde(deserialize_with = "default_on_eof")]` / `#[wincode(with = "wincode_compat::DefaultOnEmptyRead<...>")]`: [1](#0-0) 

This mirrors the exact same "optional/deprecated-tolerant" pattern used for other legacy extra fields in the same struct, such as `_unused_incremental_snapshot_persistence` and `_unused_epoch_accounts_hash`, both of which are allowed to be absent and are simply discarded: [2](#0-1) 

However, when the struct is folded into `BankFieldsToDeserialize` in `DeserializableBankSnapshot::into_fields`, the code does not treat a missing `accounts_lt_hash` as a recoverable condition — it panics: [3](#0-2) 

This is structurally the same bug class as the referenced report: a precondition check (there, "the Balancer oracle must be enabled"; here, "the snapshot must contain `accounts_lt_hash`") that the surrounding infrastructure explicitly supports being false/absent (deprecated Balancer oracle vs. a field intentionally readable-as-optional for backward/forward compatibility), but that a hard `require`/`expect()` turns into an unconditional failure. Just as the vault could never be deployed once Balancer oracles were disabled, a node attempting `bank_from_snapshot_archives` / `bank_from_snapshot_dir` on any snapshot stream lacking this field (e.g., truncated tail, or a wire/version skew where this field is not populated) will panic instead of failing gracefully, since `into_fields` is called unconditionally on the deserialize path used by both `bank_from_snapshot_archives` and `bank_from_snapshot_dir`: [4](#0-3) [5](#0-4) 

## Impact Explanation
A validator or `ledger-tool` process that attempts to load a snapshot whose trailing "extra fields" section does not carry `accounts_lt_hash` bytes (which the deserializer's own `default_on_eof`/`DefaultOnEmptyRead` machinery is built to tolerate for every other field in the struct) will hit `panic!` rather than a typed `SnapshotError`. This is a node-panic / denial-of-startup condition on the snapshot loading path, which is within scope (snapshot generation/rebuild). Because the panic path bypasses the error-returning `wincode::ReadResult` machinery that every sibling field in the same struct is designed to use safely, this is an inconsistency between the "optional, backward-compatible" design intent of the deserializer and the strict enforcement in the folding logic.

## Likelihood Explanation
Likelihood depends on whether current mainline snapshot-writers always populate `accounts_lt_hash` (in which case this is latent/defense-in-depth code) or whether there exist supported code paths/versions where it can be legitimately absent when read back (e.g., partial reads, older writer/newer reader skew, or any other path that intentionally leaves this field un-populated the way `_unused_epoch_accounts_hash` is). I was unable to fully confirm from the available index whether any currently-supported snapshot-writing path can produce a stream lacking this field for a reader on this same version — this needs verification against `runtime/src/snapshot_bank_utils.rs`'s full snapshot-writing logic and any version-compatibility matrix in `runtime/src/snapshot_utils.rs`, which the index did not fully surface.

## Recommendation
Change the panic in `DeserializableBankSnapshot::into_fields` to return a `wincode::ReadError`/`SnapshotError` instead of `.expect(...)`, consistent with how other malformed/missing-field conditions in the same function (e.g., the `unused_epoch_stakes` check just above it) are handled:
```diff
-        bank_fields.accounts_lt_hash = accounts_lt_hash
-            .expect("snapshot must have accounts_lt_hash")
-            .into();
+        let accounts_lt_hash = accounts_lt_hash.ok_or_else(|| {
+            wincode::ReadError::InvalidValue("snapshot must have accounts_lt_hash")
+        })?;
+        bank_fields.accounts_lt_hash = accounts_lt_hash.into();
```
This preserves the invariant (the field is still required for the bank to function) while turning an unrecoverable process panic into a normal, catchable snapshot-load error.

## Proof of Concept
Not independently reproduced in this session. To confirm/deny reachability, a background agent should:
1. Construct or truncate a snapshot's extra-fields tail so the bytes for `accounts_lt_hash` are absent (simulating `default_on_eof`/`DefaultOnEmptyRead` firing).
2. Call `bank_from_snapshot_archives`/`bank_from_snapshot_dir` on it and confirm the process panics with `"snapshot must have accounts_lt_hash"` instead of returning a `SnapshotError`. [6](#0-5)

### Citations

**File:** runtime/src/serde_snapshot.rs (L484-509)
```rust
struct ExtraFieldsToDeserialize {
    #[serde(deserialize_with = "default_on_eof")]
    #[wincode(with = "wincode_compat::DefaultOnEmptyRead<u64>")]
    lamports_per_signature: u64,
    #[serde(deserialize_with = "default_on_eof")]
    #[wincode(
        with = "wincode_compat::DefaultOnEmptyRead<Option<UnusedIncrementalSnapshotPersistence>>"
    )]
    _unused_incremental_snapshot_persistence: Option<UnusedIncrementalSnapshotPersistence>,
    #[serde(deserialize_with = "default_on_eof")]
    #[wincode(with = "wincode_compat::DefaultOnEmptyRead<Option<Hash>>")]
    _unused_epoch_accounts_hash: Option<Hash>,
    #[serde(deserialize_with = "default_on_eof")]
    #[wincode(
        with = "wincode_compat::DefaultOnEmptyRead<Vec<(u64, DeserializableVersionedEpochStakes)>>"
    )]
    // Match the serialize side's `HashMap<u64, VersionedEpochStakes>`, which samples `0..=1` entries.
    #[cfg_attr(
        feature = "frozen-abi",
        stable_abi_sample(with = "stable_abi::sample_collection_sized(rng, \
                                  stable_abi::context::SequenceLenMax(1))")
    )]
    versioned_epoch_stakes: Vec<(u64, DeserializableVersionedEpochStakes)>,
    #[serde(deserialize_with = "default_on_eof")]
    #[wincode(with = "wincode_compat::DefaultOnEmptyRead<Option<SerdeAccountsLtHash>>")]
    accounts_lt_hash: Option<SerdeAccountsLtHash>,
```

**File:** runtime/src/serde_snapshot.rs (L577-597)
```rust
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

**File:** runtime/src/snapshot_bank_utils.rs (L205-221)
```rust
    let mut measure_rebuild = Measure::start("rebuild bank from snapshots");
    let (bank, info) = reconstruct_bank_from_fields(
        bank_fields,
        accounts_db_fields,
        genesis_config,
        runtime_config,
        account_paths,
        storage_and_next_append_vec_id,
        debug_keys,
        leader_for_tests,
        limit_load_slot_count_from_snapshot,
        verify_index,
        accounts_db_config,
        accounts_update_notifier,
        exit,
    )?;
    measure_rebuild.stop();
```

**File:** runtime/src/snapshot_bank_utils.rs (L401-420)
```rust
    let snapshot_bank_fields = SnapshotBankFields::new(bank_fields, None);
    let snapshot_accounts_db_fields = SnapshotAccountsDbFields::new(accounts_db_fields, None);
    let ((bank, info), measure_rebuild_bank) = measure_time!(
        reconstruct_bank_from_fields(
            snapshot_bank_fields,
            snapshot_accounts_db_fields,
            genesis_config,
            runtime_config,
            account_paths,
            storage_and_next_append_vec_id,
            debug_keys,
            leader_for_tests,
            limit_load_slot_count_from_snapshot,
            verify_index,
            accounts_db_config,
            accounts_update_notifier,
            exit,
        )?,
        "rebuild bank from snapshot"
    );
```
