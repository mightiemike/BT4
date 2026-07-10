### Title
NEAR Storage-Stake Released by GC Is Permanently Locked in `BtcLightClient` Due to Missing Withdrawal Mechanism — (File: `contract/src/lib.rs`)

### Summary
`submit_blocks` collects NEAR token deposits to cover on-chain storage costs for new block headers. When `run_mainchain_gc` later deletes those headers, the storage stake previously paid by callers is released back into the contract's account balance. Because the contract has no withdrawal or sweep function, this freed NEAR accumulates permanently and is irrecoverable.

### Finding Description

`submit_blocks` is `#[payable]` and enforces a deposit equal to the net storage increase of the current call:

```rust
let amount = env::attached_deposit();
let initial_storage = env::storage_usage();
// ... submit headers, run GC ...
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
require!(amount >= required_deposit, ...);
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id()).transfer(refund).into()
}
```

The refund only returns the excess from the **current** call's net storage delta. It does not account for storage freed by GC of headers that were paid for in **previous** calls.

In NEAR Protocol, storage staking is implicit: the contract account's balance must be ≥ `storage_usage × storage_byte_cost`. When `run_mainchain_gc` removes old mainchain headers, the contract's minimum required balance drops, and the NEAR tokens that were deposited in prior calls to cover those headers become free balance in the contract account. The contract has no function to send this free balance anywhere — the only two `Promise::new` calls in the entire contract are the refund line in `submit_blocks`.

`run_mainchain_gc` removes headers from all three storage structures (`headers_pool`, `mainchain_height_to_header`, `mainchain_header_to_height`) but returns nothing to any caller:

```rust
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    // ...
    for height in start_removal_height..end_removal_height {
        let blockhash = &self.mainchain_height_to_header.get(&height)...;
        self.remove_block_header(blockhash);
        self.mainchain_height_to_header.remove(&height);
    }
    // no transfer, no refund
}
```

With the recommended `gc_threshold = 52704` (one year of Bitcoin blocks), the contract continuously cycles: relayers deposit NEAR to add headers, GC removes old ones, freed NEAR accumulates in the contract balance with no exit path.

### Impact Explanation

NEAR tokens deposited by relayers to cover storage for mainchain headers are permanently locked in the contract once those headers are garbage-collected. The amount grows monotonically over the contract's lifetime. At the recommended `gc_threshold` of 52,704 blocks and NEAR's current storage cost (~1 NEAR per 100 KB), the locked amount scales with the total volume of headers ever submitted and GC'd. There is no privileged or unprivileged path to recover these funds.

### Likelihood Explanation

This is triggered by normal, intended operation. Every production deployment runs GC automatically inside `submit_blocks` (called with `batch_size = num_of_headers`). Any relayer submitting blocks in steady-state operation will continuously cause storage to be freed and NEAR to accumulate. No adversarial action is required; the bug manifests from the first GC cycle onward.

### Recommendation

Add a permissioned withdrawal function that transfers the contract's free balance (i.e., `env::account_balance() - env::storage_usage() * env::storage_byte_cost()`) to an authorized address (e.g., `Role::DAO`):

```rust
pub fn withdraw_free_balance(&mut self, recipient: AccountId) -> Promise {
    // Only callable by DAO role
    let locked = env::storage_byte_cost()
        .saturating_mul(env::storage_usage().into());
    let free = env::account_balance().saturating_sub(locked);
    require!(free > NearToken::from_near(0), "No free balance");
    Promise::new(recipient).transfer(free)
}
```

Alternatively, track per-caller deposits and issue refunds when GC frees their headers' storage.

### Proof of Concept

1. Relayer calls `submit_blocks([h1..h100])` with deposit `D = 100 × storage_byte_cost × header_size`. Storage increases by 100 headers. `required_deposit = D`, `refund = 0`. Contract balance now holds `D`.
2. Chain grows. Later, relayer calls `submit_blocks([h101..h200])`. GC fires and removes `h1..h100`. Net storage delta = 0 (100 added, 100 removed). `required_deposit = 0`, full new deposit is refunded. But `D` from step 1 is still in the contract balance — the freed storage stake from `h1..h100` has no recipient.
3. Repeat indefinitely. The contract balance grows by `D` per GC cycle with no withdrawal path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** contract/src/lib.rs (L371-416)
```rust
    /// Public call to run GC on a mainchain.
    /// `batch_size` is how many block headers should be removed in the execution
    ///
    /// # Panics
    /// If initial blockheader or tip blockheader are not in a header pool
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

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
