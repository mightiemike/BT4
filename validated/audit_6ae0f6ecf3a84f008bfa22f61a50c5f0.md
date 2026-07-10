### Title
NEAR Tokens Freed by GC Accumulate in Contract With No Withdrawal Path — (`contract/src/lib.rs`)

### Summary

The `submit_blocks` function is `#[payable]` and always invokes `run_mainchain_gc`. When GC removes old block headers, NEAR Protocol releases the freed storage stake back into the contract's account balance. Because the contract exposes no sweep or withdrawal function, these released tokens are permanently locked. The vulnerability class is identical to M-13: assets accumulate inside a contract that has no mechanism to move them out.

### Finding Description

`submit_blocks` measures storage before and after the combined header-insertion + GC pass: [1](#0-0) 

```rust
let amount = env::attached_deposit();
let initial_storage = env::storage_usage();
// ... headers inserted ...
self.run_mainchain_gc(num_of_headers);
let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());
```

`diff_storage_usage` is the **net** change after GC. When GC removes at least as many bytes as the new headers added, `saturating_sub` clamps the result to zero, `required_deposit` becomes zero, and the full caller deposit is refunded. [2](#0-1) 

The freed storage stake — the NEAR tokens that NEAR Protocol releases back to the contract's account because its storage footprint shrank — is never forwarded anywhere. It silently accumulates in the contract's own balance. The contract has no `withdraw`, `sweep`, or governance-callable transfer function anywhere in its public API. [3](#0-2) 

GC is called on every `submit_blocks` invocation, so this accumulation occurs during normal, continuous relayer operation.

### Impact Explanation

NEAR tokens freed by GC are permanently locked in the contract's account. With `gc_threshold = 52704` (≈ one year of Bitcoin blocks) and each `ExtendedHeader` occupying on the order of hundreds of bytes, the cumulative freed storage stake over months of operation is non-trivial. No privileged or unprivileged caller can recover these tokens without a contract upgrade.

### Likelihood Explanation

This is triggered by every successful `submit_blocks` call once the chain has grown past `gc_threshold`. The relayer is designed to call `submit_blocks` continuously; the accumulation is therefore certain during normal production operation, not a corner case.

### Recommendation

Add a governance-gated (e.g., `Role::DAO`) function that transfers the contract's surplus balance to a designated treasury account:

```rust
#[access_control_any(roles(Role::DAO))]
pub fn withdraw_surplus(&mut self, recipient: AccountId, amount: NearToken) -> Promise {
    Promise::new(recipient).transfer(amount)
}
```

Alternatively, track the exact storage freed by each GC pass and immediately refund that amount to the original depositor or a configured treasury address.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 3`.
2. Submit 4 headers with a deposit covering 4 headers of storage. GC removes 1 old header.
3. Observe: `diff_storage_usage = storage_after_gc - initial_storage` = net of +3 headers. The freed storage for the 1 removed header is never returned to anyone.
4. Repeat. After N rounds, the contract's account balance exceeds what is needed for its current storage, and no function exists to extract the difference. [4](#0-3)

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

**File:** contract/src/lib.rs (L377-416)
```rust
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
