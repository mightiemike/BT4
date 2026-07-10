### Title
Storage Deposits Permanently Locked After GC — (`contract/src/lib.rs`)

### Summary
The `submit_blocks` function collects NEAR storage deposits from callers to cover the cost of storing new block headers. When `run_mainchain_gc` later removes those headers (either inline within `submit_blocks` or via a standalone public call), the storage is freed but the NEAR tokens originally deposited to cover it remain locked in the contract with no withdrawal mechanism.

### Finding Description
`submit_blocks` is `#[payable]` and computes the net storage delta within a single call to determine how much deposit to keep:

```rust
// contract/src/lib.rs lines 173-197
let amount = env::attached_deposit();
let initial_storage = env::storage_usage();
// ... headers inserted, then GC runs ...
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
require!(amount >= required_deposit, ...);
let refund = amount.saturating_sub(required_deposit);
if refund > NearToken::from_near(0) {
    Promise::new(env::predecessor_account_id()).transfer(refund).into()
}
```

This accounting is local to a single call. Consider the cross-call lifecycle:

1. **Call N**: Relayer submits 10 headers. Storage grows by `10 × header_size`. Relayer deposits `10 × header_size × byte_cost` NEAR. GC does not yet trigger (below `gc_threshold`).
2. **Call N+K**: Relayer submits 10 more headers. Storage grows by `10 × header_size`, then GC removes the 10 headers from Call N. Net `diff_storage_usage = 0` (via `saturating_sub`). Required deposit = 0. Full deposit for Call N+K is refunded.

The NEAR deposited in Call N to cover the now-GC'd headers is **never returned**. It remains in the contract's balance. There is no `withdraw`, `claim_storage_refund`, or equivalent function anywhere in the contract.

Additionally, `run_mainchain_gc` is a standalone public function (not `#[payable]`, no refund logic):

```rust
// contract/src/lib.rs lines 376-416
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { ... }
```

Any unprivileged NEAR account can call `run_mainchain_gc` directly, triggering storage release without any refund path for the original depositor.

### Impact Explanation
NEAR tokens deposited by relayers to cover storage costs accumulate permanently in the contract as headers are GC'd. With the recommended `gc_threshold = 52704` (one year of blocks) and approximately 200 bytes per `ExtendedHeader`, the steady-state storage footprint is ~10 MB, corresponding to ~100 NEAR at current storage pricing. Each full GC cycle locks one full cycle's worth of storage deposits with no recovery path. Over the operational lifetime of the contract, this compounds into a significant and irrecoverable loss for relayers.

### Likelihood Explanation
GC is triggered automatically inside every `submit_blocks` call once the chain exceeds `gc_threshold`. This is the normal, expected steady-state operation of the contract. No adversarial action is required; the loss occurs through routine relayer activity. The standalone `run_mainchain_gc` path additionally allows any unprivileged caller to accelerate the storage release without triggering any refund.

### Recommendation
Track the cumulative NEAR deposited per caller (or globally) and the cumulative storage freed by GC. When storage is freed—whether inline in `submit_blocks` or via standalone `run_mainchain_gc`—compute the released deposit value and either refund it immediately to the original depositor or credit it to a claimable balance that the relayer can withdraw later.

### Proof of Concept
1. Relayer calls `submit_blocks` with 52,704 headers (filling `gc_threshold`), depositing `D` NEAR.
2. Relayer calls `submit_blocks` with 1 more header. GC removes the oldest header. `diff_storage_usage = env::storage_usage().saturating_sub(initial_storage)` evaluates to 0 (one header added, one removed). Required deposit = 0; full new deposit refunded.
3. The `D` NEAR from step 1 covering the GC'd header remains in the contract balance.
4. Repeat: every subsequent `submit_blocks` call GC's one header and locks its proportional deposit permanently.
5. Alternatively, any account calls `run_mainchain_gc(52704)` directly, freeing all stored headers' storage in one call, locking the entire `D` NEAR with no refund path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L173-197)
```rust
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
