### Title
Legacy RentPaying Accounts Can Be Drained to 1 Lamport to Permanently Evade `clean_accounts` Zero-Lamport Garbage Collection - (File: accounts-db/src/accounts_db.rs, svm/src/rent_calculator.rs)

### Summary
The external report describes an attacker setting a numeric field (`routingFee`) to `1 wei` instead of `0` to satisfy a strict `> 0` validation while defeating the economic purpose of the check. The analogous condition in agave's AccountsDB is `AccountsDb::clean_accounts`, which only reclaims/garbage-collects an account when its balance is *exactly* zero lamports (`AccountInfo::is_zero_lamport()`), never for a nonzero "dust" balance. Combined with the SVM rent-state-transition rules that explicitly permit a pre-existing `RentPaying` account to be debited down to `1` lamport (but never fully drained to `0`), a user can keep any legacy sub-rent-exempt account alive indefinitely at `1` lamport, permanently defeating cleanup and imposing storage bloat, exactly mirroring the "set to 1 unit to dodge the >0/zero check" bug class.

### Finding Description
`AccountsDb::clean_accounts` is the sole mechanism (besides shrink-driven zero-lamport-single-ref handling) that purges garbage account entries from the index/storage. Inside its per-candidate scan it only treats an account as purgeable when the account is exactly zero lamports: [1](#0-0) 

Any account whose balance is `>= 1` lamport is treated as "not zero" (`found_not_zero += 1`) and is never scheduled for removal by this path, no matter how small the balance is or how long it has been dormant.

Separately, `transition_allowed` in the SVM rent module explicitly authorizes an already-`RentPaying` account to move to a new `RentPaying` state as long as the lamports do not increase and the data size is unchanged: [2](#0-1) 

Because `RentState::RentPaying` requires `lamports > 0` by definition (`get_account_rent_state` treats `lamports == 0` as `Uninitialized`, not `RentPaying`): [3](#0-2) 

...an account owner can debit a legacy RentPaying account down to `1` lamport (satisfying `post_lamports <= pre_lamports` and staying in the `RentPaying` post-state) but is blocked by this same transition logic from ever debiting it to exactly `0` in the same instruction context, since going from `RentPaying` to `Uninitialized` is not the failure case being restricted — actually going to `Uninitialized`/`RentExempt` is always allowed per line 190. However, a user under no obligation to zero the account can simply *choose* to leave `1` lamport in it rather than draining fully to `0`, which is trivially legal and cheaper than a full drain, and — unlike a `0`-lamport account — it will never be picked up as a "zero lamport" cleaning candidate. Rent collection itself, which historically would have eventually swept such an account to `0` and let it be reclaimed, is now globally disabled: [4](#0-3) [5](#0-4) 

With rent collection disabled and `clean_accounts`'s purge condition keyed strictly to `lamports == 0`, there is no remaining path (outside of the account owner explicitly zeroing the account) for the protocol to reclaim storage occupied by dust-balance accounts. This is architecturally identical to the reported bug: a boundary value (`1` instead of `0`) satisfies the literal validation ("nonzero"/"not garbage") but defeats the intended state-cleanup/economic guarantee.

### Impact Explanation
Any unprivileged user who controls an account (their own wallet, PDAs they can drain via a program they control, etc.) can leave it at `1` lamport instead of `0` after any transfer/close operation. Because `clean_accounts` never reclaims non-zero-lamport accounts and rent collection is permanently disabled, these dust accounts persist in `AccountsDb`'s index and storage files forever — they are never candidates for the zero-lamport purge path, only for ordinary shrink consolidation (which reduces disk footprint of *storage files* but does not remove the account from the accounts index or its associated index/bucket-map/lattice-hash bookkeeping). At scale (many accounts drained to `1` lamport instead of `0`), this produces disproportionate, unbounded growth of the accounts index, bucket map, and snapshot size relative to actual economically "alive" state, degrading clean/shrink/snapshot performance and increasing CPU/I/O and memory cost for every validator — a disproportionate storage/CPU cost impact.

### Likelihood Explanation
High likelihood: leaving `1` lamport in an account instead of fully closing it to `0` requires no special privilege, no unusual transaction construction, and is strictly *easier* than a full drain (many wallets/programs already sometimes leave dust due to rounding). No validator or operator role is needed; it's purely a byproduct of ordinary unprivileged user account management (or can be done deliberately/maliciously to bloat state).

### Recommendation
Extend `clean_accounts`'s (and the shrink zero-lamport-single-ref sweep's) purge eligibility beyond the strict `lamports == 0` check to also reclaim accounts whose balance is below a meaningful economic threshold (e.g., below the rent-exempt minimum for their data size) when they are otherwise eligible for garbage collection, or reintroduce an enforced minimum balance ratchet so that once an account's balance is reduced, it cannot be left in an indefinitely-persisting sub-threshold "RentPaying" dust state. Alternatively, disallow leaving new or existing accounts in a nonzero sub-rent-exempt state at all (forcing either `0` or `>= rent_exempt_minimum`), closing the gap between "some lamports" and "actually reclaimable."

### Proof of Concept
1. Identify (or create, pre-enforcement, or via a program you control) an account in `RentPaying` state — i.e., `0 < lamports < rent_exempt_minimum(data_len)`.
2. Submit a transaction that transfers `lamports - 1` out of the account, leaving exactly `1` lamport. This passes `transition_allowed` because `post_rent_state` is still `RentPaying` with `post_lamports (1) <= pre_lamports` and unchanged data size — see [2](#0-1) .
3. Because the account's lamports are `1` (nonzero), `AccountsDb::clean_accounts`'s scan classifies it as `found_not_zero` and never schedules it for index/storage reclamation — see [1](#0-0) .
4. Since rent collection is disabled network-wide (`disable_partitioned_rent_collection`, `update_rent_exempt_status_for_account`), no other mechanism will ever reduce this account's balance to `0` or otherwise reclaim it.
5. Repeat across many accounts/pubkeys to accumulate a growing set of permanently un-reclaimable dust entries in the accounts index/bucket map, imposing disproportionate storage and CPU cost on all validators over time.

### Citations

**File:** accounts-db/src/accounts_db.rs (L1941-1958)
```rust
                                match index_in_slot_list {
                                    Some(index_in_slot_list) => {
                                        // found info relative to max_clean_root
                                        let (slot, account_info) = &slot_list[index_in_slot_list];
                                        if account_info.is_zero_lamport() {
                                            useless = false;
                                            // The latest one is zero lamports. We may be able to purge it.
                                            // Add all the rooted entries that contain this pubkey.
                                            // We know the highest rooted entry is zero lamports.
                                            candidate_info.slot_list =
                                                self.accounts_index.get_entries_up_to_inclusive(
                                                    slot_list,
                                                    max_clean_root_inclusive,
                                                );
                                            candidate_info.ref_count = ref_count;
                                        } else {
                                            found_not_zero += 1;
                                        }
```

**File:** svm/src/rent_calculator.rs (L80-95)
```rust
pub fn get_account_rent_state(
    account_lamports: u64,
    account_size: usize,
    min_balance: u64,
) -> RentState {
    if account_lamports == 0 {
        RentState::Uninitialized
    } else if account_lamports >= min_balance {
        RentState::RentExempt
    } else {
        RentState::RentPaying {
            data_size: account_size,
            lamports: account_lamports,
        }
    }
}
```

**File:** svm/src/rent_calculator.rs (L188-207)
```rust
pub fn transition_allowed(pre_rent_state: &RentState, post_rent_state: &RentState) -> bool {
    match post_rent_state {
        RentState::Uninitialized | RentState::RentExempt => true,
        RentState::RentPaying {
            data_size: post_data_size,
            lamports: post_lamports,
        } => {
            match pre_rent_state {
                RentState::Uninitialized | RentState::RentExempt => false,
                RentState::RentPaying {
                    data_size: pre_data_size,
                    lamports: pre_lamports,
                } => {
                    // Cannot remain RentPaying if resized or credited.
                    post_data_size == pre_data_size && post_lamports <= pre_lamports
                }
            }
        }
    }
}
```

**File:** svm/src/account_loader.rs (L356-360)
```rust
    // Now that rent fee collection is disabled, we won't collect rent for any
    // account. If there are any rent paying accounts, their `rent_epoch` won't
    // change either. However, if the account itself is rent-exempted but its
    // `rent_epoch` is not u64::MAX, we will set its `rent_epoch` to u64::MAX.
    // In such case, the behavior stays the same as before.
```

**File:** feature-set/src/lib.rs (L2387-2390)
```rust
        (
            disable_partitioned_rent_collection::id(),
            "SIMD-0175: Disable partitioned rent collection #4562",
        ),
```
