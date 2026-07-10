### Title
GC Removes Mainchain Common-Ancestor From `headers_pool`, Causing `reorg_chain` to Panic and Permanently Block Canonical-Chain Updates — (`contract/src/lib.rs`)

---

### Summary

Fork headers stored via `store_fork_header` are inserted only into `headers_pool` and are **never removed by GC**. Mainchain blocks, however, are removed from both `mainchain_header_to_height` **and** `headers_pool` by `run_mainchain_gc`. When a fork's common ancestor with the mainchain is later GC'd, any subsequent call to `submit_blocks` that triggers a reorg to that fork panics inside `reorg_chain` with `"previous fork block should be there"`. Because NEAR reverts all state on panic, the contract can never promote the higher-chainwork fork to the canonical chain, permanently diverging from the real Bitcoin canonical chain.

---

### Finding Description

**`store_fork_header` omits the fork block from both mainchain index maps:** [1](#0-0) 

Fork blocks land only in `headers_pool`. They are invisible to GC.

**`run_mainchain_gc` iterates only over `mainchain_height_to_header` and calls `remove_block_header` on each entry:** [2](#0-1) 

**`remove_block_header` deletes from both `mainchain_header_to_height` and `headers_pool`:** [3](#0-2) 

So after GC, the common-ancestor block is gone from `headers_pool` entirely.

**`reorg_chain` walks back through the fork chain using `headers_pool.get`, terminating only when it finds a block in `mainchain_header_to_height`:** [4](#0-3) 

If the common ancestor was GC'd, it is no longer in `mainchain_header_to_height`, so the loop does not stop there. On the very next iteration it calls `headers_pool.get(&prev_block_hash)` where `prev_block_hash` is the GC'd common ancestor's hash — which is also absent from `headers_pool` — triggering the `unwrap_or_else` panic.

**Concrete trigger sequence:**

```
Mainchain: [genesis] → [A] → [B] → … → [M]  (tip)
Fork:      [A] → [F1] → [F2] → … → [Fn]     (stored in headers_pool only)

GC runs: removes [genesis], [A], [B] from mainchain_height_to_header AND headers_pool.

Relayer submits [Fn+1] (extends fork, chain_work > mainchain):
  submit_block_header_inner → reorg_chain([Fn+1], …)
  Loop: [Fn+1] → [Fn] → … → [F1]
  Next: headers_pool.get([A]) → None → panic!("previous fork block should be there")
```

The transaction reverts. No state is changed. The contract is permanently unable to process the reorg.

---

### Impact Explanation

The contract's `mainchain_tip_blockhash` and `mainchain_header_to_height` remain frozen on the lower-chainwork chain. Any consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will verify proofs against the stale, lower-chainwork chain rather than the actual Bitcoin canonical chain. Cross-chain bridges, atomic swaps, and lending protocols built on top of this contract will accept proofs for transactions that the real Bitcoin network has reorganized away from. [5](#0-4) 

---

### Likelihood Explanation

The scenario requires three sequential conditions, all of which arise from normal operation:

1. A relayer submits fork headers (standard behavior during any fork tracking).
2. The mainchain advances past `gc_threshold` blocks, causing GC to remove the common ancestor.
3. The fork later accumulates higher chainwork (realistic for Dogecoin/Litecoin with their shorter block times and lower hashrate, or during any real reorg on those chains).

No privileged role, leaked key, or social engineering is required. The relayer is a trusted but non-privileged actor whose normal submission pattern is sufficient to reach the broken state.

---

### Recommendation

`store_fork_header` and `run_mainchain_gc` must be made symmetric. Two concrete options:

1. **GC fork headers alongside mainchain blocks.** Track fork headers in a separate indexed structure (e.g., keyed by height) so GC can remove fork blocks whose common ancestor falls below `mainchain_initial_blockhash`.

2. **Bound the reorg walk by `mainchain_initial_blockhash`.** In `reorg_chain`, add an explicit check: if `fork_header_cursor.block_height` reaches or falls below the height of `mainchain_initial_blockhash`, abort with a clear error rather than panicking on a missing pool entry. This prevents the panic and surfaces the condition explicitly.

Either fix must ensure that a fork whose common ancestor has been GC'd is rejected cleanly at submission time (preferred) rather than causing a panic inside `reorg_chain`.

---

### Proof of Concept

```
State after GC (gc_threshold = 3, mainchain at height 5):
  mainchain_height_to_header: {3→H3, 4→H4, 5→H5}
  mainchain_header_to_height: {H3→3, H4→4, H5→5}
  headers_pool:               {H3, H4, H5, F2, F3}   ← F2/F3 are fork blocks at heights 2,3
  mainchain_initial_blockhash = H3
  (H0, H1, H2 removed by GC from both maps and headers_pool)

Fork: H0 → F1(h=1) → F2(h=2) → F3(h=3)
  F1 was never submitted (or was also GC'd from headers_pool if it was mainchain)
  F2 and F3 are in headers_pool (fork blocks, never GC'd)

Relayer submits F4 (h=4, chain_work > H5.chain_work):
  get_prev_header(F4) → headers_pool.get(F3) → OK
  submit_block_header_inner: fork path, chain_work > mainchain → reorg_chain(F4, 5)

reorg_chain:
  cursor = F4; not in mainchain_header_to_height → insert, remove H4, cursor = F3
  cursor = F3; not in mainchain_header_to_height → insert, remove H3, cursor = F2
  cursor = F2; not in mainchain_header_to_height → insert, no H2 to remove
    prev_block_hash = F1.hash
    headers_pool.get(F1.hash) → None (F1 was never stored or was GC'd)
    → panic!("previous fork block should be there")
```

Transaction reverts. `mainchain_tip_blockhash` remains H5. The contract can never reach F4 regardless of how many times the relayer retries.

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

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

**File:** contract/src/lib.rs (L616-642)
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
```

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
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
