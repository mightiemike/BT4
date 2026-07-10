### Title
Freed Storage Deposits Permanently Locked After GC With No Withdrawal Mechanism - (`contract/src/lib.rs`)

### Summary

`submit_blocks()` collects NEAR storage-staking deposits from relayers to cover the cost of storing new block headers. When `run_mainchain_gc()` later removes those headers, the storage is freed and the corresponding NEAR tokens become liquid in the contract's balance. However, the contract exposes no function to withdraw these freed tokens, so they accumulate permanently with no extraction path — an exact structural analog to M-12.

### Finding Description

`submit_blocks()` is `#[payable]` and enforces that the caller covers the net storage growth:

```
required_deposit = storage_byte_cost * (storage_after - storage_before)
```

Only the surplus above `required_deposit` is refunded to the caller. The portion equal to `required_deposit` stays in the contract to satisfy NEAR's storage-staking requirement for the newly written entries (`mainchain_height_to_header`, `mainchain_header_to_height`, `headers_pool`). [1](#0-0) 

`run_mainchain_gc()` is called inside every `submit_blocks()` invocation and also callable directly. It removes old mainchain entries once the stored window exceeds `gc_threshold`: [2](#0-1) 

`remove_block_header()` deletes entries from `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

When these storage slots are deleted, NEAR Protocol reduces the contract's minimum required balance proportionally, converting the previously locked staking tokens into liquid contract balance. The contract has **no function** — privileged or otherwise — to transfer this recovered balance out. The only `Promise::transfer` in the entire contract is the per-call excess refund inside `submit_blocks()`: [4](#0-3) 

There is no `withdraw`, `claim_storage_refund`, or any DAO-callable transfer function anywhere in `contract/src/lib.rs` or the other source files.



### Impact Explanation

Every block header stored costs approximately `storage_byte_cost × (size of ExtendedHeader entries)` in NEAR. With `gc_threshold = 52704` (one year of Bitcoin blocks, as recommended in the init docs), the contract continuously cycles: relayers pay deposits for new headers, GC removes old ones, and the freed NEAR accumulates in the contract balance. Over the lifetime of the deployment this compounds into a material sum of NEAR tokens that are irrecoverably locked — no role, including `Role::DAO`, can extract them without a contract upgrade. [5](#0-4) 

### Likelihood Explanation

This is triggered automatically and continuously. Every call to `submit_blocks()` that causes GC to run (i.e., once the chain exceeds `gc_threshold` blocks) frees storage and grows the locked balance. No adversarial action is required; the normal relayer operation is sufficient. The relayer is explicitly designed to call `submit_blocks()` in a continuous loop. [6](#0-5) 

### Recommendation

Add a privileged withdrawal function (callable by `Role::DAO`) that transfers the contract's excess liquid balance — i.e., `env::account_balance() - env::storage_byte_cost() * env::storage_usage()` — to a designated beneficiary. This mirrors the `withdrawProfits()` pattern recommended in the M-12 report for `RageDnDepository`.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100`.
2. Relayer submits 101 blocks, each batch with the required deposit. GC removes block 0; its storage entries are deleted; the freed NEAR becomes liquid in the contract balance.
3. Query `env::account_balance()` vs. `env::storage_byte_cost() * env::storage_usage()` — the difference is non-zero and growing.
4. Attempt to call any contract method to transfer this balance out — none exists. The tokens are permanently locked until a contract upgrade is deployed. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L131-131)
```rust
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
```

**File:** contract/src/lib.rs (L166-197)
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
