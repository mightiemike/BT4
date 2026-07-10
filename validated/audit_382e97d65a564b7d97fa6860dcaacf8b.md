### Title
Storage Deposits Permanently Locked After Garbage Collection — (`contract/src/lib.rs`)

### Summary
`submit_blocks` collects NEAR storage deposits from callers to cover the cost of persisting block headers. When `run_mainchain_gc` later removes those same headers and frees the storage, the corresponding NEAR tokens are never returned to any party. There is no per-depositor accounting and no claiming function, so every yoctoNEAR paid for a block that is eventually garbage-collected is permanently locked inside the contract.

### Finding Description
`submit_blocks` is marked `#[payable]` and enforces that the caller attaches enough NEAR to cover the net new storage consumed by the submitted headers. [1](#0-0) 

The deposit calculation is:

```
diff_storage_usage = env::storage_usage() - initial_storage   // net bytes added
required_deposit   = storage_byte_cost * diff_storage_usage
refund             = amount - required_deposit                 // returned immediately
```

Any excess above `required_deposit` is refunded to `env::predecessor_account_id()` at call time. The exact amount needed to store the new headers is retained by the contract. [2](#0-1) 

Later, `run_mainchain_gc` removes the oldest mainchain headers once the chain exceeds `gc_threshold`: [3](#0-2) 

`remove_block_header` deletes entries from `mainchain_header_to_height` and `headers_pool`, freeing the on-chain storage bytes: [4](#0-3) 

When NEAR storage is freed, the protocol releases the corresponding staked NEAR back to the **contract account**. However, the contract:

1. Keeps no per-depositor balance map (the `BtcLightClient` struct has no such field).
2. Has no function to transfer freed storage deposits back to the original submitter or any designated recipient.
3. Has no global accounting variable tracking how much NEAR has been freed by GC. [5](#0-4) 

The freed NEAR accumulates in the contract's own balance with no path out.

### Impact Explanation
Every relayer or unprivileged caller who paid a storage deposit for a block header that is subsequently garbage-collected permanently loses that NEAR. Over the lifetime of a production deployment with `gc_threshold = 52704` (≈ one year of Bitcoin blocks), the locked amount grows continuously. At NEAR's storage cost of ~1 NEAR per 100 KB, storing ~52 704 headers (each ~80 bytes + overhead ≈ ~200 bytes) costs on the order of tens of NEAR per year, all of which becomes irrecoverable once GC runs.

### Likelihood Explanation
GC is designed to run automatically inside every `submit_blocks` call once the chain exceeds `gc_threshold`. [6](#0-5) 

Any unprivileged caller submitting headers triggers GC. In a live deployment the relayer submits blocks continuously, so GC fires on every batch after the threshold is reached. The locked-funds condition is therefore reached in normal operation, not only under adversarial conditions.

### Recommendation
Track per-depositor (or global) freed-storage balances. When `run_mainchain_gc` removes headers, compute the storage bytes freed, convert to NEAR at `env::storage_byte_cost()`, and either (a) immediately transfer that amount back to the original depositor (requires a depositor map keyed by block hash) or (b) credit a claimable balance for the submitter of each removed block. Add an access-controlled `claim_storage_refund` function so depositors can withdraw their freed deposits.

### Proof of Concept

1. Relayer calls `submit_blocks([h1, h2, ..., h100])` with `deposit = 100 * storage_byte_cost * bytes_per_header`. Contract stores all 100 headers; exact deposit is retained.
2. Chain grows past `gc_threshold`. On the next `submit_blocks` call, `run_mainchain_gc` removes `h1..hN` from storage.
3. NEAR protocol releases the staked NEAR for those freed bytes back to the contract account balance.
4. No code path in the contract transfers or records this freed NEAR for the original depositor.
5. The relayer's NEAR is permanently locked; calling any view or mutating function provides no way to recover it. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
