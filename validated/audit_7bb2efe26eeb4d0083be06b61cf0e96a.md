### Title
`submit_blocks` Is Blocked for `UnrestrictedSubmitBlocks` Callers When Contract Is Paused Due to Nested `run_mainchain_gc` Pause Guard - (File: `contract/src/lib.rs`)

### Summary

`submit_blocks` is decorated with `#[pause]` and grants a bypass to callers holding the `UnrestrictedSubmitBlocks` role. However, it unconditionally calls `self.run_mainchain_gc(num_of_headers)` internally. `run_mainchain_gc` carries its own independent `#[pause(except(roles(Role::UnrestrictedRunGC)))]` guard. When the contract is paused, a caller with `UnrestrictedSubmitBlocks` (but not `UnrestrictedRunGC`) will pass the outer guard on `submit_blocks`, process all headers, and then panic inside `run_mainchain_gc`, reverting the entire transaction. The `UnrestrictedSubmitBlocks` bypass is therefore rendered inoperative during a pause.

### Finding Description

`submit_blocks` is the sole public entry point for advancing the light-client chain state. It is decorated with `#[pause]`, which the `near_plugins` crate implements by injecting a guard at the top of the function body that panics unless the contract is unpaused **or** the `env::predecessor_account_id()` holds one of the listed exception roles. [1](#0-0) 

The exception role for `submit_blocks` is `UnrestrictedSubmitBlocks`. [2](#0-1) 

After processing all submitted headers, `submit_blocks` unconditionally calls `self.run_mainchain_gc(num_of_headers)`: [3](#0-2) 

`run_mainchain_gc` is a public method with its **own** independent pause guard, but with a **different** exception role — `UnrestrictedRunGC`: [4](#0-3) 

Because `near_plugins` injects the pause check into the function body (not at the call site), the check executes on every invocation — including internal Rust method calls — using the same `env::predecessor_account_id()` as the outer call. When the contract is paused and the caller holds `UnrestrictedSubmitBlocks` but not `UnrestrictedRunGC`, the flow is:

1. `submit_blocks` pause guard → passes (caller has `UnrestrictedSubmitBlocks`)
2. All headers are processed and stored
3. `self.run_mainchain_gc(num_of_headers)` is called
4. `run_mainchain_gc` pause guard → **panics** (caller lacks `UnrestrictedRunGC`)
5. Entire transaction reverts; no headers are committed

The two roles are defined separately and there is no documented requirement that a holder of `UnrestrictedSubmitBlocks` must also hold `UnrestrictedRunGC`: [5](#0-4) 

### Impact Explanation

High within its scope: the `UnrestrictedSubmitBlocks` role exists precisely to allow trusted relayers to keep the light-client chain advancing during an emergency pause. Because `submit_blocks` always calls `run_mainchain_gc` and that call always fails for any caller without `UnrestrictedRunGC`, **no account can successfully call `submit_blocks` while the contract is paused**, regardless of which bypass roles it holds. The chain tip freezes, and any downstream consumer relying on `verify_transaction_inclusion` for confirmations is also blocked.

### Likelihood Explanation

Low: the contract must be paused (an intentional, rare administrative action) for the bug to manifest. However, the pause scenario is exactly when the `UnrestrictedSubmitBlocks` bypass is most needed, making the failure particularly harmful when it does occur.

### Recommendation

Either:

1. **Remove the `#[pause]` guard from `run_mainchain_gc`** when it is called internally from `submit_blocks` by extracting the GC logic into a private helper (e.g., `run_mainchain_gc_inner`) that has no pause guard, and have both the public `run_mainchain_gc` and `submit_blocks` call that helper.
2. **Align the exception roles**: document and enforce that any account granted `UnrestrictedSubmitBlocks` must also be granted `UnrestrictedRunGC`, or merge the two roles.

Option 1 is structurally cleaner and eliminates the hidden coupling between the two pause guards.

### Proof of Concept

1. Deploy the contract and pause it (via a `PauseManager` account).
2. Grant an account only the `UnrestrictedSubmitBlocks` role (not `UnrestrictedRunGC`).
3. Have that account call `submit_blocks` with a valid batch of headers and sufficient deposit.
4. Observe: the transaction reverts with the `near_plugins` pause error originating inside `run_mainchain_gc`, even though the caller was explicitly authorized to submit blocks during a pause.
5. Confirm: granting the same account `UnrestrictedRunGC` in addition resolves the revert, proving the root cause is the mismatched exception roles between the two nested pause guards. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L40-46)
```rust
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
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

**File:** contract/src/lib.rs (L166-181)
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
