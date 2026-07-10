### Title
Fork Reorg Walk-Back Panics on GC'd Common Ancestor, Permanently Locking Canonical Chain at Lower Chainwork — (`contract/src/lib.rs`)

---

### Summary

`reorg_chain` walks back through `headers_pool` to find the common ancestor between a fork and the mainchain. `run_mainchain_gc` removes old mainchain blocks from `headers_pool`. Fork blocks are **never** GC'd. If the common ancestor was GC'd before a fork tip with higher chainwork is submitted, `reorg_chain` panics unconditionally, making it impossible to ever promote the higher-chainwork fork. The contract's canonical chain is permanently frozen at a lower-chainwork state.

---

### Finding Description

Three storage asymmetries create the root cause:

**1. Fork blocks are stored only in `headers_pool`:** [1](#0-0) 

**2. Mainchain blocks are stored in all three maps:** [2](#0-1) 

**3. GC removes mainchain blocks from `headers_pool` via `remove_block_header`, but fork blocks are never touched by GC:** [3](#0-2) [4](#0-3) 

**4. `reorg_chain` terminates its walk-back by checking `mainchain_header_to_height`, but fetches each ancestor unconditionally from `headers_pool`:** [5](#0-4) 

The loop condition `!self.mainchain_header_to_height.contains_key(...)` will never be satisfied for a GC'd common ancestor because `remove_block_header` also removes it from `mainchain_header_to_height`. The loop then advances the cursor by calling `self.headers_pool.get(&prev_block_hash).unwrap_or_else(|| env::panic_str("previous fork block should be there"))`. When the cursor reaches the fork block whose `prev_block_hash` is the GC'd common ancestor, that hash is absent from `headers_pool` and the contract panics.

---

### Impact Explanation

The NEAR transaction that triggers the reorg panics and is rolled back. The fork blocks remain in `headers_pool` (they are never GC'd), but the mainchain tip is never updated. Every subsequent attempt to submit a fork block with higher chainwork will re-enter `reorg_chain` and panic identically. The contract is permanently stuck tracking a lower-chainwork chain. The broken invariant is: **the contract must always reflect the chain with the highest cumulative `chain_work`**. After this condition is reached, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will return results relative to the wrong canonical chain, corrupting all downstream SPV proof decisions.

---

### Likelihood Explanation

The scenario requires only normal relayer behavior:

- Fork blocks are routinely submitted before the common ancestor is GC'd (the relayer follows the Bitcoin network and submits competing tips).
- `run_mainchain_gc` is called automatically inside every `submit_blocks` invocation, so GC advances without any special action.
- Once the common ancestor falls outside `gc_threshold`, it is removed. Any subsequent fork block that crosses the chainwork threshold triggers the panic.

No privileged access, leaked keys, or social engineering is required. Any unprivileged NEAR account that can call `submit_blocks` can reach this state.

---

### Recommendation

1. **Bound the reorg walk-back at `mainchain_initial_blockhash`.** Before advancing the cursor, check whether `prev_block_hash` is the hash of a block older than `mainchain_initial_blockhash`. If so, abort the reorg with a clear error rather than panicking on a missing pool entry.

2. **Reject fork submissions whose divergence point is below `mainchain_initial_blockhash`.** In `submit_block_header_inner`, before calling `store_fork_header`, verify that the fork's eventual common ancestor is still within the GC window. This prevents orphaned fork chains from accumulating.

3. **GC fork blocks alongside mainchain blocks.** Track fork block hashes with their divergence height so they can be pruned when the divergence point is GC'd.

---

### Proof of Concept

```
gc_threshold = 10

Step 1 – Init: genesis (height 0) + mainchain blocks 1–5 submitted.
         headers_pool contains: genesis, m1, m2, m3, m4, m5
         mainchain_initial_blockhash = genesis

Step 2 – Submit fork blocks F1–F5 (prev of F1 = genesis).
         genesis is still in headers_pool → get_prev_header succeeds.
         F1–F5 stored via store_fork_header (headers_pool only).

Step 3 – Submit mainchain blocks m6–m15.
         run_mainchain_gc fires; amount_of_headers_we_store = 16 > 10.
         Removes heights 0–5 (genesis through m5) from headers_pool
         via remove_block_header.
         mainchain_initial_blockhash advances to m6.
         Fork blocks F1–F5 are untouched (not in mainchain_height_to_header).

Step 4 – Submit fork block F6 (prev = F5, still in headers_pool).
         get_prev_header(F6) → F5 found ✓
         F6.chain_work > mainchain tip chain_work → reorg_chain called.

Step 5 – reorg_chain walk-back:
         cursor = F6 → not in mainchain_header_to_height → advance
         cursor = F5 → not in mainchain_header_to_height → advance
         cursor = F4 → not in mainchain_header_to_height → advance
         cursor = F3 → not in mainchain_header_to_height → advance
         cursor = F2 → not in mainchain_header_to_height → advance
         cursor = F1 → not in mainchain_header_to_height → advance
         next = headers_pool.get(genesis_hash) → ABSENT (GC'd)
         → panic!("previous fork block should be there")

Result: transaction rolled back; mainchain tip stays at m15 (lower chainwork).
        Every future attempt to promote this fork panics identically.
        verify_transaction_inclusion operates on the wrong canonical chain.
```

### Citations

**File:** contract/src/lib.rs (L401-414)
```rust
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
```

**File:** contract/src/lib.rs (L616-643)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```
