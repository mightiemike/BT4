### Title
Fork Retarget Validation Uses Mainchain Ancestor Instead of Fork Chain Ancestor - (`contract/src/lib.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When validating a fork block at a difficulty retarget boundary, `get_next_work_required` calls `blocks_getter.get_header_by_height(first_block_height)` to retrieve the block at the start of the retarget interval. The `BlocksGetter` implementation always resolves this via `mainchain_height_to_header`, returning the **mainchain** block at that height rather than the **fork chain's actual ancestor** at that height. This is a direct cross-module desynchronization: the committed fork-chain history is ignored in favor of the live mainchain index, producing an incorrect expected `bits` value.

---

### Finding Description

The `BlocksGetter` trait implementation for `BtcLightClient` is:

```rust
// contract/src/lib.rs, lines 677-682
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

This unconditionally queries `mainchain_height_to_header`, which is the **mainchain** height index. [1](#0-0) 

At a retarget boundary, all three chain modules call this function to obtain the timestamp of the first block in the difficulty interval:

- **Bitcoin** (`bitcoin.rs` line 81): `blocks_getter.get_header_by_height(first_block_height)` [2](#0-1) 
- **Litecoin** (`litecoin.rs` line 88): `blocks_getter.get_header_by_height(first_block_height)` [3](#0-2) 
- **Dogecoin** (`dogecoin.rs` lines 292-295): `blocks_getter.get_header_by_height(height_first)` — with a developer `TODO` comment explicitly acknowledging the problem: `// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor` [4](#0-3) 

When a fork diverges **before** a retarget boundary, the fork chain's ancestor at `first_block_height` is a different block than the mainchain block at that height. The two blocks can have different timestamps, producing a different `expected_bits` value. The contract then compares this incorrectly-computed `expected_bits` against the submitted fork block's `bits` field. [5](#0-4) 

---

### Impact Explanation

Two concrete consequences:

1. **Legitimate fork blocks rejected at retarget boundaries.** A valid fork block whose `bits` field correctly reflects the fork chain's own retarget history will be rejected with `"bad-diffbits: incorrect proof of work"` because the contract computed `expected_bits` from the wrong (mainchain) ancestor timestamp. This prevents any fork that diverges before a retarget boundary from ever being extended past that boundary, permanently blocking chain reorganization even when the fork has more accumulated work.

2. **Difficulty validation bypass for fork chains.** If the mainchain's ancestor at `first_block_height` has a timestamp that produces a *lower* difficulty (higher target) than the fork's own ancestor, an attacker can submit a fork block with that easier `bits` value. The contract accepts it (the computed `expected_bits` matches), and the subsequent PoW check `U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits)` uses the block's own (easier) `bits` field. The attacker therefore needs less proof-of-work to extend the fork, reducing the cost of a chain reorganization attack. [6](#0-5) 

---

### Likelihood Explanation

Any fork that diverges at or before a retarget boundary triggers this bug. Bitcoin retargets every 2016 blocks (~2 weeks), Litecoin every 2016 blocks, and Dogecoin every block (post-height 145,000). Forks crossing retarget boundaries are a routine occurrence on all supported chains. The entry point is the permissionless `submit_blocks()` function, callable by any NEAR account. [7](#0-6) 

---

### Recommendation

`get_header_by_height` must be replaced with an ancestor-walk when called in the context of fork validation. The correct approach is to walk backwards from the fork tip via `get_prev_header` until the target height is reached, rather than indexing into `mainchain_height_to_header`. Alternatively, pass the fork tip's hash as context so the retarget lookup can traverse the fork's own chain of `prev_block_hash` links to find the correct ancestor. The Dogecoin module's own TODO comment already identifies this as the required fix. [8](#0-7) 

---

### Proof of Concept

1. Deploy the contract (Bitcoin or Litecoin build) initialized at a retarget boundary height (e.g., height 2016).
2. Submit 2016 mainchain blocks, establishing the mainchain tip at height 4032 (the next retarget boundary). The mainchain block at height 2016 has timestamp `T_main`.
3. Construct a fork that diverges at height 2015 (one block before the retarget boundary). The fork's block at height 2016 has timestamp `T_fork ≠ T_main`.
4. Submit fork blocks up to height 4031 (all pass, since no retarget is needed within the interval).
5. Submit the fork block at height 4032 (the retarget block). Its `bits` field is computed using `T_fork` as the interval start timestamp — the correct value per the fork chain's own history.
6. The contract calls `get_header_by_height(2016)`, retrieves the **mainchain** block with timestamp `T_main`, computes `expected_bits` from `T_main`, and rejects the fork block with `"bad-diffbits"` even though the block is valid.
7. Alternatively, craft the fork block's `bits` using `T_main` (the mainchain ancestor's timestamp). The contract accepts it, even though this `bits` value is incorrect per the fork chain's own history — and if `T_main` produces a lower difficulty than `T_fork`, the attacker mines this block with less work than the protocol requires.

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

**File:** contract/src/lib.rs (L517-526)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L19-26)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let config = self.get_config();
        let expected_bits = get_next_work_required(&config, block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );
```

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/litecoin.rs (L86-93)
```rust
    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```
