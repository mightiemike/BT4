### Title
Dogecoin AuxPoW version flag not enforced when `aux_data` is absent, allowing AuxPoW-flagged blocks to bypass chain validation - (File: `contract/src/dogecoin.rs`)

### Summary
The Dogecoin `submit_block_header` function accepts blocks that carry the `BLOCK_VERSION_AUXPOW` flag in their version field even when no `AuxData` is supplied. Such blocks are validated using only a direct PoW check (block hash < target), bypassing the full AuxPoW chain validation. A relayer supplying adversarial chain data can exploit this to inject a fake Dogecoin block into the light client's canonical chain.

### Finding Description
In `contract/src/dogecoin.rs`, `submit_block_header` dispatches on the presence of `aux_data`:

```rust
if let Some(ref aux_data) = aux_data {
    self.check_aux(&block_header, aux_data);
} else {
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        format!("block should have correct pow")
    );
}
``` [1](#0-0) 

`check_aux` enforces that when AuxPoW data IS present, the block's version must have `BLOCK_VERSION_AUXPOW` (0x100) set:

```rust
require!(
    block_header.version & BLOCK_VERSION_AUXPOW != 0,
    "Aux POW block does not have AuxPoW flag set in version"
);
``` [2](#0-1) 

However, the `else` branch (no AuxPoW data) does **not** enforce the inverse: that the block's version must **not** have `BLOCK_VERSION_AUXPOW` set. After Dogecoin's AuxPoW activation (block 371337 on mainnet), all valid Dogecoin blocks have `version & 0x100 != 0`. A relayer can submit such a block with `aux_data = None`, and the contract will accept it using only direct PoW verification, without validating the AuxPoW chain.

The `check_target` / `check_pow` path for Dogecoin checks difficulty, timestamps, and target bits — but contains no check on the version flag: [3](#0-2) 

This is the direct analog to the external report: just as `_podWithdrawalCredentials()` only constructs a `0x01`-prefixed credential and never a `0x02`-prefixed one, the Dogecoin contract only enforces the AuxPoW type constraint in one direction (AuxPoW data present → version flag must be set) but not the other (version flag set → AuxPoW data must be present).

### Impact Explanation
An attacker who can mine a Dogecoin block header with `version & 0x100 != 0` and a hash below the current target can submit it to the contract with `aux_data = None`. The contract accepts this block as valid, stores it in `headers_pool`, and — if its chainwork exceeds the current tip — promotes it to the canonical chain via `submit_block_header_inner`. This corrupts the canonical chain state tracked by `mainchain_tip_blockhash` / `mainchain_height_to_header`, and enables false SPV proofs returned by `verify_transaction_inclusion_v2` for transactions in the fake chain. [4](#0-3) 

### Likelihood Explanation
Medium-low. The attacker must mine a block header with the AuxPoW flag set and a hash below the Dogecoin target. Dogecoin's difficulty is orders of magnitude lower than Bitcoin's, making this feasible for a well-resourced attacker. The attacker must also be a trusted relayer (`submit_blocks` is gated by `#[trusted_relayer]`), which is an economic barrier (staking) rather than a privileged-role barrier, and is therefore reachable from the relayer-path entry point explicitly listed in scope. [5](#0-4) 

### Recommendation
Add an explicit check in the `else` branch of `submit_block_header` that the block's version does not carry the `BLOCK_VERSION_AUXPOW` flag:

```rust
} else {
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "Non-AuxPoW block must not have AuxPoW flag set in version"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        format!("block should have correct pow")
    );
}
```

### Proof of Concept
1. Craft a Dogecoin block header with `version = 0x00620100` (chain ID 0x0062, AuxPoW flag 0x100 set), valid `prev_block_hash` pointing to a known chain tip, and `bits` matching the expected difficulty.
2. Iterate the `nonce` field until `double_sha256(header_bytes) < target_from_bits(bits)` — this is standard direct PoW mining at Dogecoin difficulty.
3. Submit `[(header, None)]` to `submit_blocks` as a trusted relayer.
4. The contract skips `check_aux`, passes the direct PoW check, and stores the block in `headers_pool`. If chainwork exceeds the current tip, `reorg_chain` promotes it to the canonical chain.
5. Subsequent calls to `verify_transaction_inclusion_v2` against this block height return `true` for attacker-crafted transaction proofs. [6](#0-5)

### Citations

**File:** contract/src/dogecoin.rs (L23-47)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let expected_bits =
            get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );

        // Check timestamp against median time past of the previous 11 blocks
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Reject blocks whose timestamp is more than 2 hours ahead of local time
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
    }
```

**File:** contract/src/dogecoin.rs (L52-61)
```rust
        const BLOCK_VERSION_AUXPOW: i32 = 0x100;

        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );
```

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
    }
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```
