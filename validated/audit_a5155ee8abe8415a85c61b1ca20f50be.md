### Title
Unprivileged Caller Can Invoke `run_mainchain_gc` to Permanently Evict Blocks Within the Confirmation Window, Causing `verify_transaction_inclusion` to Panic — (`contract/src/lib.rs`, `btc-types/src/contract_args.rs`)

---

### Summary

`run_mainchain_gc` carries no access-control guard beyond the pause gate. Any NEAR account can call it. When the mainchain holds `gc_threshold + 1` blocks, a single call with `batch_size = 1` removes the oldest block (`H_old`) from both `mainchain_header_to_height` and `headers_pool`. A subsequent `verify_transaction_inclusion` call for `H_old` with `confirmations = gc_threshold` passes the guard `confirmations <= gc_threshold` but then panics at the `mainchain_header_to_height` lookup with `"block does not belong to the current main chain"`, because the block no longer exists in storage. The removal is irreversible; the block cannot be re-submitted.

---

### Finding Description

**Access control on `run_mainchain_gc`**

The public method is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. There is no `#[trusted_relayer]`, `#[private]`, or role-based gate. When the contract is not paused, every NEAR account can call it with an arbitrary `batch_size`. [1](#0-0) 

Compare with `submit_blocks`, which carries `#[trusted_relayer]`: [2](#0-1) 

**What GC deletes**

`remove_block_header` erases the block from both `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

Inside `run_mainchain_gc`, every height in `[start_removal_height, end_removal_height)` is deleted, and `mainchain_initial_blockhash` is advanced: [4](#0-3) 

**The broken invariant in `verify_transaction_inclusion`**

The only guard meant to ensure the target block is still in storage is: [5](#0-4) 

This check is necessary but not sufficient. GC removes blocks that have *more* than `gc_threshold` confirmations (they are the oldest blocks, beyond the retention window), yet a caller can legitimately request `confirmations = gc_threshold` for such a block. The lookup that follows panics: [6](#0-5) 

`verify_transaction_inclusion_v2` is equally affected because it delegates to `verify_transaction_inclusion` after its own coinbase-proof check: [7](#0-6) 

**Concrete state walk**

| Step | State |
|------|-------|
| Chain initialized with `gc_threshold + 1` blocks (heights 0 … gc_threshold) | `H_old` = hash at height 0; tip at height `gc_threshold` |
| Attacker calls `run_mainchain_gc(1)` | `amount_of_headers_we_store = gc_threshold + 1 > gc_threshold`; removes height 0 (`H_old`) from both maps; advances `mainchain_initial_blockhash` to height 1 |
| User calls `verify_transaction_inclusion({tx_block_blockhash: H_old, confirmations: gc_threshold})` | Guard `gc_threshold <= gc_threshold` passes; `mainchain_header_to_height.get(H_old)` returns `None`; contract panics with `"block does not belong to the current main chain"` |

`H_old` had `gc_threshold + 1` confirmations at the time of the call — more than the requested `gc_threshold` — so the proof was canonically valid. The panic is permanent; the block cannot be re-inserted. [8](#0-7) 

---

### Impact Explanation

Any legitimate user whose transaction sits in the oldest retained block is permanently locked out of SPV proof verification. The block is gone from both indexes; no re-submission path exists. Downstream contracts or bridges that rely on `verify_transaction_inclusion` returning `true` will be permanently broken for those transactions.

---

### Likelihood Explanation

The attack requires no special role, no staked deposit, and no cryptographic capability. Any NEAR account can call `run_mainchain_gc` at any time the contract is unpaused. The attacker does not need to race in the concurrency sense; they simply submit a `run_mainchain_gc` transaction before the victim's verification transaction is processed. On a live chain where new blocks arrive continuously, the oldest block is always a candidate for removal.

---

### Recommendation

1. **Restrict `run_mainchain_gc` to a privileged role** (e.g., add `#[trusted_relayer]` or a dedicated `GCManager` role), so that only authorized relayers or the DAO can trigger manual GC.
2. **Alternatively, tighten the confirmation guard** in `verify_transaction_inclusion` to check that the target block's height is ≥ `mainchain_initial_blockhash` height, and return a clear error (not a panic) when the block has been GC'd.
3. **Document the invariant gap** explicitly: the current `confirmations <= gc_threshold` check does not guarantee the block is still in storage if GC has run since the block fell outside the retention window.

---

### Proof of Concept

```rust
// Pseudocode state-machine test (skip_pow_verification = true)
let gc_threshold = 5u64;
// Submit gc_threshold + 1 = 6 blocks (heights 0..=5)
let h_old = block_hash_at_height(0);

// Any unprivileged account calls:
contract.run_mainchain_gc(1);
// → removes height 0 (H_old) from mainchain_header_to_height and headers_pool
// → mainchain_initial_blockhash now points to height 1

// Legitimate user tries to verify a tx in H_old with confirmations = gc_threshold:
let result = contract.verify_transaction_inclusion(ProofArgs {
    tx_id: some_tx,
    tx_block_blockhash: h_old,   // oldest block, now GC'd
    tx_index: 0,
    merkle_proof: valid_proof,
    confirmations: gc_threshold, // = 5 ≤ gc_threshold = 5 → guard passes
});
// → panics: "block does not belong to the current main chain"
// The transaction is permanently unverifiable.
``` [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L167-169)
```rust
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L298-301)
```rust
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
