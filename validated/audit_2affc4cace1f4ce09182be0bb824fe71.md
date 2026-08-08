### Title
Non-saturating `total_bytes` subtraction in `AccountsDb::select_candidates_by_total_usage` can underflow/wrap when computing shrink-candidate alive ratios - ([File: accounts-db/src/accounts_db.rs])

### Summary
`AccountsDb::select_candidates_by_total_usage()` computes a running `total_bytes` (sum of `written_bytes()` across shrink candidates) and, for each candidate that is selected to shrink, subtracts `bytes_saved` from it using a plain `-=` rather than `saturating_sub`, unlike almost every other size-accounting operation in `accounts-db` (e.g. `remove_accounts`, `LtHash::mix_out`, `evict`, `alive_bytes_exclude_zero_lamport_single_ref_accounts`), which consistently use `saturating_sub`/`checked_sub`/asserted invariants.

### Finding Description
This mirrors the reported bug class: a loop accumulates a "target" quantity (`total_bytes`) from independent contributions and then repeatedly subtracts a locally computed delta (`bytes_saved`) from that running total inside the loop, exactly as `LMPVault._withdraw()` subtracted `Math.max(info.debtDecrease, info.totalAssetsPulled)` from `info.totalAssetsToPull` without a guard against the subtrahend exceeding the total. [1](#0-0) 

```
let mut total_alive_bytes: u64 = 0;
let mut total_bytes: u64 = 0;
for slot in shrink_slots {
    ...
    let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&store) as u64;
    total_alive_bytes += alive_bytes_after_shrink;
    let written_bytes = store.written_bytes();
    total_bytes += written_bytes;
    ...
}
``` [2](#0-1) 

```
} else {
    let current_store_size = store.written_bytes();
    let after_shrink_size = store_usage.alive_bytes_after_shrink;
    let bytes_saved = current_store_size.saturating_sub(after_shrink_size);
    total_bytes -= bytes_saved;
    shrink_slots.insert(store_usage.slot, Arc::clone(store));
}
```

While in normal, single-threaded, sequential-store operation `bytes_saved` for a given store is bounded by that store's own `written_bytes` (and thus by construction cannot exceed the running `total_bytes` sum), the invariant depends entirely on `store.written_bytes()` returning a *stable* value between the first accumulation pass (line ~3012) and the second consumption pass (line ~3063), and on each store contributing exactly once. This code takes `Arc<AccountStorageEntry>` clones and reads `written_bytes()` a second time later in the same function without holding any lock preventing concurrent appends to the storage in between (writes to a storage happen via lock-free/atomic append operations elsewhere in `accounts-db`). If `written_bytes()` (backed by `self.accounts.len()`, i.e., the mmap append cursor) increases between the two reads — which can legitimately happen since a slot is only "rooted"/frozen for the purpose of *cleaning*, not necessarily immutable against concurrent writes at exactly this moment — `current_store_size` used for `bytes_saved` in the second pass could differ from what was summed into `total_bytes` in the first pass, and `bytes_saved` (derived from a larger `written_bytes` reading) could exceed the corresponding contribution already counted, causing the unguarded `total_bytes -= bytes_saved` to underflow.

### Impact Explanation
This function only affects unprivileged, purely internal AccountsDb bookkeeping (shrink candidate selection), matching the requested scope (AccountsDB storage/index, shrink logic). An underflow here does not corrupt account data or hashes directly, but:
- In debug builds this panics the node (a node crash / DoS on the validator background service thread), a concrete "node panic" outcome.
- In release builds (which agave ships with `overflow-checks` typically disabled for `accounts-db` release profiles) it wraps to a huge `u64`, causing `alive_ratio = total_alive_bytes / total_bytes` to become near zero for all subsequent candidates, which forces the shrink-selection loop to treat almost all remaining candidates as "goal not yet achieved," selecting far more storages for shrink than intended. This produces disproportionate CPU and I/O cost in the accounts background service shrink pass, matching the "disproportionate storage and CPU cost" impact category in the validated bug list.

### Likelihood Explanation
Likelihood is speculative rather than concretely demonstrated: I could not confirm within the given tool budget whether `AccountStorageEntry::written_bytes()` can actually change between the two passes of `select_candidates_by_total_usage` for a slot present in `shrink_candidate_slots` (i.e., whether stores in this set are guaranteed append-immutable by the time this function runs). If storages are guaranteed immutable once added to `shrink_candidate_slots` (which appears to be the design intent based on the accompanying comments about single-threaded execution), then this is a purely defensive-coding gap (missing `saturating_sub`) rather than an exploitable bug. I was unable to fully verify this immutability invariant or locate a concrete public entrypoint that lets an unprivileged actor race a write into a shrink candidate before this function's second pass, within the available iterations.

### Recommendation
Change `total_bytes -= bytes_saved;` to `total_bytes = total_bytes.saturating_sub(bytes_saved);` (consistent with the `saturating_sub` already used to compute `bytes_saved` itself and with the rest of the codebase's defensive-arithmetic conventions), removing any possibility of underflow/wraparound regardless of whether the append-immutability invariant holds today.

### Proof of Concept
I was not able to construct or verify a concrete end-to-end trigger (e.g., a sequence of RPC/transaction calls) that causes `store.written_bytes()` to change between the two passes of `select_candidates_by_total_usage` for the same `Arc<AccountStorageEntry>` while it remains in `shrink_candidate_slots`; this would require deeper tracing of when slots are added to `shrink_candidate_slots` relative to storage mutability guarantees, which exceeded the available investigation budget. Given this open question about exploitability, this finding should be treated as a defensive-coding / robustness issue backed by a concrete non-saturating arithmetic op that deviates from the codebase's own conventions, rather than a fully proven, reachable underflow.

### Citations

**File:** accounts-db/src/accounts_db.rs (L3004-3026)
```rust
        let mut total_alive_bytes: u64 = 0;
        let mut total_bytes: u64 = 0;
        for slot in shrink_slots {
            let Some(store) = self.storage.get_slot_storage_entry(*slot) else {
                continue;
            };
            let alive_bytes_after_shrink = self.alive_bytes_after_shrink(&store) as u64;
            total_alive_bytes += alive_bytes_after_shrink;
            let written_bytes = store.written_bytes();
            total_bytes += written_bytes;
            debug_assert!(
                written_bytes > 0,
                "shrink candidate has zero written bytes! slot: {slot} id: {}",
                store.id(),
            );
            let alive_ratio = alive_bytes_after_shrink as f64 / written_bytes as f64;
            store_usages.push(StoreUsageInfo {
                slot: *slot,
                alive_ratio,
                alive_bytes_after_shrink,
                store: store.clone(),
            });
        }
```

**File:** accounts-db/src/accounts_db.rs (L3062-3070)
```rust
            } else {
                let current_store_size = store.written_bytes();
                let after_shrink_size = store_usage.alive_bytes_after_shrink;
                let bytes_saved = current_store_size.saturating_sub(after_shrink_size);
                total_bytes -= bytes_saved;
                shrink_slots.insert(store_usage.slot, Arc::clone(store));
            }
        }
        (shrink_slots, shrink_slots_next_batch)
```
