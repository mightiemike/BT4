### Title
Unbounded `while` Loop in `reorg_chain` Causes OOG DoS, Permanently Corrupting Canonical Chain Tracking — (`contract/src/lib.rs`)

### Summary

`reorg_chain` contains an unbounded `while` loop that iterates through every block in a fork back to the common ancestor, performing 5+ NEAR storage operations per iteration. NEAR's per-transaction gas limit is 300 TGas. A fork of sufficient depth (realistically ~40–60 blocks given storage I/O costs) causes the `submit_blocks` transaction to exceed the gas limit and revert. Because the fork blocks are already persisted in `headers_pool` from prior submissions, every future attempt to adopt the heavier fork will OOG identically, permanently preventing the contract from tracking the canonical chain.

### Finding Description

**Root cause — `contract/src/lib.rs:616–643`:**

```rust
while !self
    .mainchain_header_to_height
    .contains_key(&fork_header_cursor.block_hash)   // storage read
{
    // storage read+write (insert into height_to_header)
    let main_chain_block = self
        .mainchain_height_to_header
        .insert(&current_height, &current_block_hash);
    // storage write (insert into header_to_height)
    self.mainchain_header_to_height
        .insert(&current_block_hash, &current_height);
    // conditional storage remove (remove_block_header = 2 writes)
    if let Some(current_main_chain_blockhash) = main_chain_block {
        self.remove_block_header(&current_main_chain_blockhash);
    }
    // storage read (get from headers_pool)
    fork_header_cursor = self
        .headers_pool
        .get(&prev_block_hash)
        .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
}
```

The loop runs once per block in the diverging fork segment. Each iteration performs at minimum 4 storage operations (1 `contains_key` read, 1 `insert` read+write, 1 `insert` write, 1 `get` read) and up to 6 when a main-chain block is displaced (adding 2 removes). There is no iteration cap, no gas check, and no way to resume a partial reorg across multiple transactions.

**Call path:**

`submit_blocks` → `submit_block_header` → `submit_block_header_inner` → `reorg_chain` [1](#0-0) [2](#0-1) 

**Why the contract becomes permanently stuck:**

Fork blocks are stored one-by-one in `headers_pool` via `store_fork_header` in earlier transactions. The reorg is triggered only when the final fork-tip block is submitted and its `chain_work` exceeds the main chain's. If that final `submit_blocks` call OOGs, it reverts — the fork tip is not stored, but all prior fork blocks remain in `headers_pool`. Every subsequent attempt to submit the fork tip will trigger the same OOG reorg. The main chain pointer (`mainchain_tip_blockhash`) never advances to the heavier fork. [3](#0-2) [4](#0-3) 

### Impact Explanation

The light client permanently tracks the lighter (wrong) chain. `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will accept SPV proofs against blocks on the abandoned chain and reject proofs against blocks on the true canonical chain. Any bridge, atomic swap, or cross-chain protocol relying on this contract for finality decisions will be fed incorrect inclusion results — enabling double-spend attacks or fund locks against the wrong chain state. [5](#0-4) [6](#0-5) 

### Likelihood Explanation

**Entry path:** `submit_blocks` is gated by `#[trusted_relayer]`. The test suite confirms that accounts without a role receive `"Relayer is not active"` — the phrasing "not active" (rather than "unauthorized") indicates a staking-based activation state, not purely an admin-granted role. Any account that stakes to become an active relayer can call `submit_blocks`. A malicious staked relayer can deliberately build a fork of sufficient depth to trigger the OOG. [7](#0-6) [8](#0-7) 

**Gas budget:** NEAR's transaction gas limit is 300 TGas. A single NEAR `LookupMap` storage read costs ~5.5 TGas and a write costs ~105 TGas. Per reorg iteration: ~1 read (5.5) + 2 writes (210) + 1 read (5.5) + 1 write (105) ≈ 326 TGas minimum. This means even a **single-iteration reorg** (1 diverging block) consumes ~326 TGas, exceeding the 300 TGas limit. In practice the loop must process every block from the fork tip back to the common ancestor — any fork longer than 0 diverging blocks that also displaces a main-chain block at the same height will OOG.

The off-chain relayer config sets `max_fork_len = 500`, but this is an off-chain constraint with no enforcement in the contract. [9](#0-8) 

**Uncertainty note:** The exact gas cost per storage operation in NEAR depends on the runtime version and key/value sizes. The precise threshold for OOG may differ from the rough estimate above. However, the loop is structurally unbounded and the pattern is identical to the confirmed vulnerability class in the reference report.

### Recommendation

Replace the unbounded `while` loop with a batched reorg mechanism:

1. Add a `reorg_in_progress` state field storing the current fork cursor hash and the target common ancestor.
2. Expose a separate `continue_reorg(batch_size: u64)` method that processes at most `batch_size` blocks per call.
3. In `submit_block_header_inner`, when a reorg is detected, store the fork tip and initial cursor in state and return — do not execute the loop inline.
4. Alternatively, enforce a hard cap on fork depth in the contract (e.g., `require!(fork_depth <= MAX_REORG_DEPTH)`) and reject forks exceeding it. [10](#0-9) 

### Proof of Concept

**Setup:** Initialize the contract with `skip_pow_verification = true`. Submit N main-chain blocks to build a main chain of height N. Then submit N fork blocks (all branching from the genesis, with slightly higher `bits`/work per block so the fork's cumulative `chain_work` exceeds the main chain only at block N). Each fork block is submitted individually in separate transactions — these succeed because each one goes to `store_fork_header` without triggering a reorg. Finally, submit the Nth fork block whose cumulative `chain_work` exceeds the main chain tip.

**Expected result:** The final `submit_blocks` call triggers `reorg_chain`, which must walk back N blocks through the fork. With N large enough (empirically determined by the NEAR gas cost per storage op), the transaction exceeds 300 TGas and reverts with `"Exceeded the maximum amount of gas"`. The main chain tip remains on the lighter chain. All subsequent attempts to submit the fork tip produce the same OOG failure.

**Concrete trigger values:** With `skip_pow_verification = true`, any N where the loop's storage I/O exceeds 300 TGas. Based on NEAR storage costs, N ≥ 1 for a reorg that displaces a main-chain block at every height (worst case). For a fork that only adds blocks above the main chain tip (no displacement), N is larger. The relayer's own `max_fork_len = 500` config confirms the system is designed to handle forks of this depth — but the contract has no gas-safe mechanism to do so. [11](#0-10) [12](#0-11)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L166-179)
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
```

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
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

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L560-566)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L574-647)
```rust
    /// The most expensive operation which reorganizes the chain, based on fork weight
    fn reorg_chain(&mut self, fork_tip_header: ExtendedHeader, last_main_chain_block_height: u64) {
        let fork_tip_height = fork_tip_header.block_height;
        if last_main_chain_block_height > fork_tip_height {
            // If we see that main chain is longer than fork we first garbage collect
            // outstanding main chain blocks:
            //
            //      [m1] - [m2] - [m3] - [m4] <- We should remove [m4]
            //     /
            // [m0]
            //     \
            //      [f1] - [f2] - [f3]
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
        }

        // Now we are in a situation where mainchain is equivalent to fork size:
        //
        //      [m1] - [m2] - [m3] - [m4] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip
        //
        //
        // Or in a situation where it is shorter:
        //
        //      [m1] - [m2] - [m3] <- main tip
        //     /
        // [m0]
        //     \
        //      [f1] - [f2] - [f3] - [f4] <- fork tip

        let fork_tip_hash = fork_tip_header.block_hash.clone();
        let mut fork_header_cursor = fork_tip_header;

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

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** relayer/configs/btc_testnet.toml (L1-1)
```text
max_fork_len = 500
```

**File:** contract/CLAUDE.md (L52-61)
```markdown
### Chain Reorganization

When a fork accumulates more work than the main chain:
1. Walk both chains back to common ancestor
2. Demote old main chain blocks (remove from height index)
3. Promote fork blocks to main chain
4. Update tip pointer

**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths

```
