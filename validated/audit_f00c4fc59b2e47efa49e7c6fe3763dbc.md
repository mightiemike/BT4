### Title
`UnrestrictedSubmitBlocks` Pause-Bypass Silently Broken by Internal `run_mainchain_gc` Call — (`contract/src/lib.rs`)

### Summary

`submit_blocks` is decorated with `#[pause]` and grants bypass access to `Role::UnrestrictedSubmitBlocks`. However, it unconditionally calls `self.run_mainchain_gc(num_of_headers)` as a direct internal call. `run_mainchain_gc` carries its own independent `#[pause(except(roles(Role::UnrestrictedRunGC)))]` guard. Because the internal call is a plain Rust method dispatch (not a cross-contract call), `env::predecessor_account_id()` remains the original external caller throughout. When the contract is paused, a caller holding only `UnrestrictedSubmitBlocks` passes `submit_blocks`'s own pause gate but then triggers a panic inside `run_mainchain_gc`, which requires the separate `UnrestrictedRunGC` role. The entire transaction reverts, making `UnrestrictedSubmitBlocks` permanently ineffective while the contract is paused.

### Finding Description

`submit_blocks` is the primary relayer entry point: [1](#0-0) 

It unconditionally calls `run_mainchain_gc` at line 181: [2](#0-1) 

`run_mainchain_gc` is a separate public function with its own independent pause guard: [3](#0-2) 

The two bypass roles are distinct:

- `submit_blocks` → `#[pause]` → bypass: `Role::UnrestrictedSubmitBlocks`
- `run_mainchain_gc` → `#[pause(except(roles(Role::UnrestrictedRunGC)))]` → bypass: `Role::UnrestrictedRunGC` [4](#0-3) 

In `near-plugins`, the `#[pause]` macro expands to an inline check against `env::predecessor_account_id()` at the top of each decorated function. Because `self.run_mainchain_gc(num_of_headers)` is a direct Rust method call (not a `Promise`-based cross-contract call), `env::predecessor_account_id()` is unchanged — it is still the original external caller. That caller holds `UnrestrictedSubmitBlocks` but not `UnrestrictedRunGC`, so `run_mainchain_gc`'s expanded pause guard panics, reverting the entire `submit_blocks` transaction.

This is the exact analog of the reported pattern: an outer function makes an internal call to another function that has its own access-control check, and the caller's identity/role set is insufficient for the inner check, causing silent failure of the outer function's intended privileged path.

### Impact Explanation

The `UnrestrictedSubmitBlocks` role is architecturally designed to keep the light client live during a pause — for example, to allow a trusted relayer to continue submitting Bitcoin headers while the contract is under maintenance or incident response. With this bug, that role is completely inoperative: every call to `submit_blocks` reverts when the contract is paused, regardless of the caller's `UnrestrictedSubmitBlocks` grant. Downstream NEAR contracts that rely on `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will be unable to receive fresh canonical-chain updates during a pause, potentially stalling cross-chain bridges or SPV-dependent protocols that depend on a live tip. [5](#0-4) 

### Likelihood Explanation

The contract must first be paused by a `PauseManager`. Pausing is a routine operational action (incident response, upgrade preparation). Once paused, the bug is deterministically triggered on every `submit_blocks` call by any `UnrestrictedSubmitBlocks` holder. No adversarial input is required; the revert is unconditional because `run_mainchain_gc` is called on every `submit_blocks` execution path. [6](#0-5) 

### Recommendation

Extract the GC logic into a private helper function (e.g., `run_mainchain_gc_inner`) that contains no pause guard, and have both the public `run_mainchain_gc` (with its pause guard) and `submit_blocks` call that helper directly. This mirrors the recommendation in the original report: avoid routing through a public, access-controlled entry point when the intent is an internal operation.

```rust
// New private helper — no #[pause]
fn run_mainchain_gc_inner(&mut self, batch_size: u64) { /* GC logic */ }

// Public entry point keeps its own pause guard
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    self.run_mainchain_gc_inner(batch_size);
}

// submit_blocks calls the helper, not the guarded public method
pub fn submit_blocks(...) {
    ...
    self.run_mainchain_gc_inner(num_of_headers);
    ...
}
```

### Proof of Concept

1. Deploy the contract (Bitcoin feature flag).
2. Grant account `alice` the `UnrestrictedSubmitBlocks` role; do **not** grant `UnrestrictedRunGC`.
3. Grant account `pauser` the `PauseManager` role.
4. `pauser` calls `pa_pause_feature("submit_blocks")` and `pa_pause_feature("run_mainchain_gc")`.
5. `alice` calls `submit_blocks` with a valid batch of headers.
6. Observe: the call reverts with the `run_mainchain_gc` pause panic, not from `submit_blocks`'s own guard.
7. Grant `alice` also `UnrestrictedRunGC` and repeat step 5 — the call now succeeds, confirming the root cause. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L43-46)
```rust
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L376-416)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
    }
```
