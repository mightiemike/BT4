[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L881-890)
```text
    /// Whether the multisig timelock feature is enabled.
    const MULTISIG_TIMELOCK: u64 = 115;

    public fun get_multisig_timelock_feature(): u64 {
        MULTISIG_TIMELOCK
    }

    public fun is_multisig_timelock_enabled(): bool {
        is_enabled(MULTISIG_TIMELOCK)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.spec.move (L290-301)
```text
    spec upsert_timelock(multisig_account: &signer, timelock_period: u64, override_threshold: Option<u64>) {
        use std::signer;
        use std::features;
        pragma aborts_if_is_partial;
        let multisig_address = signer::address_of(multisig_account);
        let timelock_enabled = features::spec_multisig_timelock_enabled();
        // Feature flag must be enabled.
        aborts_if !features::spec_multisig_timelock_enabled();
        // Must be a multisig account.
        aborts_if !exists<MultisigAccount>(multisig_address);
        // Timelock must be enabled
        ensures timelock_enabled;
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L906-918)
```text
    /// Upsert the timelock configuration for the multisig account.
    /// timelock_period must be between MIN_TIMELOCK_PERIOD and MAX_TIMELOCK_PERIOD.
    /// override_threshold, if provided, must be > num_signatures_required and <= the number of owners.
    ///
    /// Note on pending transactions: the timelock check measures elapsed time from a transaction's
    /// `creation_time_secs`, not from when the timelock was activated. Because multisig transactions
    /// execute strictly in sequence order, this is only observable for transactions queued *after*
    /// this `upsert_timelock` call but *before* it executes — those transactions may become
    /// executable sooner than `timelock_period` seconds after this call takes effect, because part
    /// of their elapsed time is counted from before the new timelock was live. Transactions queued
    /// after this call has executed receive the full `timelock_period` protection. This residual
    /// window is bounded by the previous timelock period (or by approval time, if there was no
    /// prior timelock) and is considered an acceptable, operator-visible risk.
```
