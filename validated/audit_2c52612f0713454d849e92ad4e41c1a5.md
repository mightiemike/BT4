### Title
Stale Proof-Verification Result Due to Chain Reorganization Between Cross-Contract Call and Callback — (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` return a point-in-time boolean snapshot of mainchain state. In NEAR's asynchronous receipt model, a consuming bridge contract that calls either function and acts on the result in a subsequent callback receipt can be exploited if a trusted relayer submits a chain reorganization between those two receipts. The light client contract has no proof-consumption record, no atomic verify-and-consume primitive, and no staleness-detection mechanism, so the `true` result is irrevocably delivered to the callback even after the underlying block has been evicted from the canonical chain.

### Finding Description
`verify_transaction_inclusion` (lines 288–323, `contract/src/lib.rs`) performs three checks against live contract state:

1. It reads `mainchain_tip_blockhash` to get the current tip height.
2. It reads `mainchain_header_to_height` to confirm the target block is on the current mainchain.
3. It computes the Merkle proof against the stored `merkle_root`. [1](#0-0) 

All three reads reflect the state at the moment the receipt executes. The function returns a plain `bool` with no side-effect: it does not mark the proof as consumed, does not record the block hash, and does not emit any event that a downstream contract could use to detect a subsequent reorg.

`verify_transaction_inclusion_v2` delegates to the deprecated v1 after validating the coinbase proof, so it inherits the same stateless design. [2](#0-1) 

`submit_blocks` (lines 169–198) is callable by any trusted relayer at any time. When a fork with higher cumulative chainwork is submitted, `reorg_chain` (lines 575–647) removes the previously canonical blocks from both `mainchain_height_to_header` and `mainchain_header_to_height` via `remove_block_header`. [3](#0-2) 

In NEAR's execution model, a cross-contract call and its callback are separate receipts. Between receipt N (the `verify_transaction_inclusion` call) and receipt N+k (the callback), any number of other transactions—including a `submit_blocks` call that triggers a reorg—can be processed. The callback receives the `true` value that was computed against the pre-reorg state and has no way to know the chain has since changed.

### Impact Explanation
A consuming bridge contract (the canonical production consumer of this API, explicitly listed as an in-scope entrypoint) that releases Bitcoin-pegged assets upon receiving `true` from `verify_transaction_inclusion` can be made to release those assets for a Bitcoin transaction that no longer exists on the canonical chain after a reorganization. This is a direct double-spend: the attacker's Bitcoin transaction is confirmed on a temporary fork, the bridge releases the pegged asset, and the canonical chain (with more chainwork) then replaces the fork, erasing the original transaction. The attacker retains both the pegged asset and the original Bitcoin.

### Likelihood Explanation
- **Bitcoin mainnet**: Low. A reorg deep enough to remove a block with several confirmations requires substantial hashpower. However, shallow reorgs (1–2 blocks) occur naturally and are sufficient if the bridge uses low confirmation counts.
- **Litecoin / Dogecoin**: Medium. These chains have significantly less hashpower, making deliberate short reorgs more feasible.
- **`skip_pow_verification = true`** (a supported deployment flag): High. Any trusted relayer can submit an arbitrary fork with zero computational cost, making the race trivially exploitable. [4](#0-3) 

### Recommendation
1. **Add proof-consumption tracking**: Introduce a `LookupSet<(H256, H256)>` (block hash, tx id) that records every proof that has been verified and returned `true`. Reject re-verification of the same `(tx_block_blockhash, tx_id)` pair, and expose a method for consuming contracts to atomically verify-and-mark.
2. **Expose a finality depth parameter at the contract level**: Enforce a minimum confirmation count that is large enough to make reorgs statistically negligible, and document the residual risk for consuming contracts.
3. **Emit a structured event** on every successful verification so that consuming contracts can subscribe to reorg events and invalidate cached results.
4. **Document the staleness invariant** explicitly in the NatSpec of both `verify_transaction_inclusion` functions so that integrators understand that the returned `bool` is not a durable commitment.

### Proof of Concept
```
Block timeline (NEAR receipts):

Receipt 1 – Bridge calls verify_transaction_inclusion(tx_id=TX1, tx_block_blockhash=B, confirmations=3)
  → mainchain state: ... → [B(h=100)] → [h=101] → [h=102] → tip[h=103]
  → confirmation check: 103 - 100 + 1 = 4 >= 3  ✓
  → Merkle proof valid  ✓
  → returns true
  → schedules callback receipt with result=true

Receipt 2 – Trusted relayer calls submit_blocks([fork blocks with higher chainwork])
  → reorg_chain() executes
  → remove_block_header(B) removes B from mainchain_header_to_height and headers_pool
  → new mainchain tip is on the fork; block B no longer exists

Receipt 3 – Bridge callback fires with result=true
  → Bridge releases pegged tokens to attacker
  → TX1 does not exist on the canonical Bitcoin chain
  → Attacker retains both the pegged tokens and the original BTC
```

The root cause is entirely within `contract/src/lib.rs`: `verify_transaction_inclusion` reads live state and returns a non-durable boolean with no consumption record, making its result inherently racy against any subsequent `submit_blocks` call that triggers `reorg_chain`. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L110-112)
```rust
    // If we should run all the block checks or not
    skip_pow_verification: bool,

```

**File:** contract/src/lib.rs (L295-322)
```rust
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
```

**File:** contract/src/lib.rs (L347-369)
```rust
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

**File:** contract/src/lib.rs (L563-567)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L575-647)
```rust
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

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
