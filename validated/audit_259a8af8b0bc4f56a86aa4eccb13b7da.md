### Title
Any Unprivileged Caller Can Instantly Drain All Excess Mainchain Headers via `run_mainchain_gc` - (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` carries no caller-side access control when the contract is unpaused. Any NEAR account can invoke it with `batch_size = u64::MAX`, instantly removing every mainchain header that exceeds `gc_threshold` in a single transaction. Downstream contracts or users whose pending `verify_transaction_inclusion` calls target those now-deleted blocks will receive a hard panic rather than a proof result, breaking the light-client guarantee they relied on.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`. [1](#0-0) 

When the contract is **not** paused this decorator is a no-op for access control: any NEAR account may call the function. The `batch_size` argument is fully attacker-controlled. [2](#0-1) 

The function computes `total_amount_to_remove = stored_headers - gc_threshold` and then removes `min(total_amount_to_remove, batch_size)` headers. Passing `u64::MAX` as `batch_size` collapses the `min` to `total_amount_to_remove`, so every header above the threshold is deleted in one call. [3](#0-2) 

Each deleted header is erased from both `mainchain_header_to_height` and `headers_pool` via `remove_block_header`. [4](#0-3) 

Under normal operation the GC runs gradually: `submit_blocks` calls `run_mainchain_gc(num_of_headers)`, so at most as many headers are pruned as were just submitted. [5](#0-4) 

An attacker bypasses this gradual pacing entirely.

---

### Impact Explanation

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`) look up the target block in `mainchain_header_to_height` and panic with `"block does not belong to the current main chain"` if the entry is absent. [6](#0-5) 

Any downstream NEAR contract that calls `verify_transaction_inclusion` for a block that was in storage moments before the attack—but is now deleted—receives a cross-contract panic instead of a boolean result. If that contract gates a token release, bridge withdrawal, or settlement on a `true` result, the operation is permanently blocked for that block: the header is gone and cannot be re-submitted (the relayer would need to re-anchor from a new genesis). The invariant broken is: *"a block that was validly stored and had not yet been garbage-collected by the protocol's own pacing logic remains available for proof verification until the protocol itself removes it."*

---

### Likelihood Explanation

The entry point is a public, permissionless NEAR call requiring no stake, no role, and no special key. The only cost is gas. Any actor who benefits from blocking a specific Bitcoin-to-NEAR settlement (e.g., a counterparty in a cross-chain swap) has a direct financial incentive to call `run_mainchain_gc(u64::MAX)` at the moment the victim's block crosses the `gc_threshold` age boundary but before the victim submits their proof. The attack is cheap, deterministic, and leaves no on-chain trace beyond a normal contract call.

---

### Recommendation

Add an explicit caller restriction to `run_mainchain_gc` so that only accounts holding a designated role (e.g., `Role::UnrestrictedRunGC`, or a new `GCManager` role) may invoke it directly. The automatic invocation from within `submit_blocks` is already safe because it is bounded by `num_of_headers`. The public entry point should be gated:

```rust
// Before
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }

// After
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[access_control_any(roles(Role::UnrestrictedRunGC, Role::DAO))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }
```

Alternatively, expose only the internal call (from `submit_blocks`) and remove the public entry point entirely, relying on automatic GC pacing.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704` and sync it to Bitcoin mainnet height 100 000. The mainchain now stores 100 000 headers; 47 296 are above the threshold.
2. A user's downstream contract is about to call `verify_transaction_inclusion` for a transaction in block 40 000 (60 000 blocks old, still in storage).
3. Attacker calls:
   ```
   near call <contract> run_mainchain_gc '{"batch_size": 18446744073709551615}' --accountId attacker.near
   ```
4. The contract removes all 47 296 excess headers in one transaction. Block 40 000 is deleted from `mainchain_header_to_height` and `headers_pool`.
5. The user's downstream contract calls `verify_transaction_inclusion`; the lookup at line 300 panics with `"block does not belong to the current main chain"`.
6. The user's settlement, withdrawal, or bridge operation is permanently blocked for that block. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L181-181)
```rust
        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L298-301)
```rust
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
