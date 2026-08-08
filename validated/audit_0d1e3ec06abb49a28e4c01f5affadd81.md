### Title
Unchecked subtraction of `accounts_data_len_from_duplicates` from `total_accum.accounts_data_len` in `generate_index` can underflow/silently corrupt accounts data length - (File: accounts-db/src/accounts_db.rs)

### Summary
During snapshot restore, `AccountsDb::generate_index` computes a global `accounts_data_len` accumulator while scanning storages, then subtracts the amount attributable to duplicate pubkey entries once duplicates have been visited and unref'd. The capitalization counterpart of this same reduction uses `checked_sub().expect(...)` to hard-fail on any inconsistency, but the parallel `accounts_data_len` subtraction is a raw `-=` operation, exactly mirroring the reported `LineOfCredit` pattern where one side of an accounting pair (`principalUsd`) is not defensively validated/updated like its sibling and a bare subtraction is performed on a value that can be smaller than the subtrahend.

### Finding Description
`generate_index` accumulates `total_accum.accounts_data_len` across all storages during startup indexing, then, after identifying and processing pubkeys that exist in more than one slot (duplicates), reduces the running total by `accounts_data_len_from_duplicates`, the summed data length of all superseded ("duplicate") versions of accounts found via `visit_duplicate_pubkeys_during_startup`.

Immediately preceding this line, the equivalent operation for `capitalization` is guarded: [1](#0-0) 

Note the asymmetry: `capitalization` uses `checked_sub(...).expect("capitalization cannot underflow")` while `accounts_data_len` uses a plain `-=`. If `accounts_data_len_from_duplicates` (computed independently, by a separate parallel fold over `unique_pubkeys_by_bin`) is ever larger than the currently accumulated `total_accum.accounts_data_len` — due to a bug in duplicate detection/accounting, an unexpected storage/snapshot layout, or any divergence between how the two accumulators are populated during the parallel storage scan — this subtraction will underflow. Depending on build profile, this either panics (denial of validator startup / snapshot load) or, if overflow checks are not enabled for the build, silently wraps to a huge `u64` value, corrupting `AccountsDb`'s reported `accounts_data_len` for the entire life of that instance without immediately crashing.

`accounts_data_len` is a security- and consensus-relevant value: it feeds into the accounts-data-length sysvar/consensus-critical size accounting used for rent and storage limits. A silently corrupted value (via wraparound) could allow far more account storage growth than intended, or (via panic) could hard-stop restart/snapshot loading, which is the "node panic" and "disproportionate storage cost" impact classes called out in scope.

### Impact Explanation
Two possible outcomes if `accounts_data_len_from_duplicates` ever exceeds the running total:
1. If debug/overflow-checked build: `generate_index` panics, meaning a validator cannot come up from a snapshot with these particular duplicate/storage conditions — a node panic during honest replay/startup.
2. If build has overflow checks disabled (typical `--release` builds unless `overflow-checks=true` is explicitly enabled — this was not confirmed for this repo's `Cargo.toml` release profile in the time available), the subtraction silently wraps to a near-`u64::MAX` value, permanently corrupting the tracked `accounts_data_len` for that node's `AccountsDb`, which can diverge accounts-data-length accounting from other honest nodes and permit unbounded growth in account storage relative to the enforced limit.

Both outcomes match validated impact classes: node panic, or silent corruption of a consensus/limit-relevant accounting value causing storage-cost/hash divergence potential.

### Likelihood Explanation
This code runs unconditionally on every `generate_index` call, which executes on every validator startup that loads accounts from storages/snapshots. It requires no external input from an attacker to reach — it's triggered purely by the existing snapshot's contents. Because I could not fully verify (within tool budget) whether `accounts_data_len_from_duplicates` can, by construction of `visit_duplicate_pubkeys_during_startup`, ever exceed `total_accum.accounts_data_len` under any legitimate snapshot state (versus being provably bounded, as capitalization's analogous checked path suggests the authors thought was possible for that accumulator), likelihood is uncertain — the presence of the `checked_sub` guard on the sibling `capitalization` accumulator, but not on `accounts_data_len`, is itself circumstantial evidence that the authors did not intend this subtraction to be unconditionally safe, or that the asymmetry is at minimum an omitted defensive check.

### Recommendation
Apply the same defensive pattern used for `capitalization` to `accounts_data_len`:
```rust
total_accum.accounts_data_len = total_accum
    .accounts_data_len
    .checked_sub(accounts_data_len_from_duplicates)
    .expect("accounts_data_len cannot underflow");
```
This converts a potential silent wraparound into a clear, debuggable panic consistent with the neighboring capitalization check, and should be paired with an audit of `visit_duplicate_pubkeys_during_startup` to confirm the invariant `accounts_data_len_from_duplicates <= total_accum.accounts_data_len` actually holds for all valid snapshot inputs.

### Proof of Concept
I was not able to construct or verify a concrete triggering snapshot/storage layout within the available tool budget — this requires either fuzzing `generate_index` with crafted duplicate-pubkey storage layouts or deeper tracing of how `total_accum.accounts_data_len` and `accounts_data_len_from_duplicates` are independently populated across the parallel storage scan (`total_accum` accumulation) versus the duplicate-visiting pass (`visit_duplicate_pubkeys_during_startup`), to determine whether the two can diverge. This should be validated further; the finding here is a demonstrated asymmetric/missing-guard code pattern directly analogous to the reported bug class, not a confirmed exploited underflow.

### Citations

**File:** accounts-db/src/accounts_db.rs (L6107-6112)
```rust
        total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
        total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
```
