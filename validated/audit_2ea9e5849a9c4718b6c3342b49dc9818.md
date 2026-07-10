### Title
Missing AuxPoW Enforcement Check Allows AuxPoW-Flagged Blocks to Bypass Merged-Mining Validation — (File: contract/src/dogecoin.rs)

---

### Summary

In `contract/src/dogecoin.rs`, the `submit_block_header` function accepts an optional `aux_data: Option<AuxData>` parameter. When `aux_data` is `None`, the code falls back to checking the Dogecoin block's own PoW hash against the target. However, it never checks whether `block_header.version & BLOCK_VERSION_AUXPOW != 0`. If a block carries the AuxPoW version flag but is submitted without AuxPoW data, the entire merged-mining validation chain is silently skipped, and only the block's own hash is checked — which is the wrong PoW check for an AuxPoW block.

---

### Finding Description

The `submit_block_header` function in the Dogecoin build path branches on whether `aux_data` is `Some` or `None`: [1](#0-0) 

When `aux_data` is `Some`, `check_aux` is called, which enforces the full AuxPoW invariant set: [2](#0-1) 

Specifically, `check_aux` verifies:
1. The block's version has `BLOCK_VERSION_AUXPOW` set.
2. The chain ID matches the configured Dogecoin chain ID.
3. The parent block does **not** carry the Dogecoin chain ID.
4. The Dogecoin block hash appears in the parent coinbase via a valid chain merkle proof.
5. The **parent Bitcoin block's** hash satisfies the PoW target. [3](#0-2) 

When `aux_data` is `None`, none of these checks run. Instead, the code checks the Dogecoin block's **own** hash: [4](#0-3) 

The critical missing guard is: **there is no check that `block_header.version & BLOCK_VERSION_AUXPOW == 0` before taking the non-AuxPoW path.** For an AuxPoW block, the Dogecoin block header's nonce is not used for PoW mining — the PoW is performed by the parent Bitcoin block. The Dogecoin block's own hash is therefore unconstrained by the mining process and is not the correct PoW witness.

The analog to the external report is exact:

| External Report | This Repository |
|---|---|
| `optional_royalty_pct` used without checking `metadata.token_standard` | `aux_data = None` path taken without checking `block_header.version & BLOCK_VERSION_AUXPOW` |
| Token standard enforces a specific royalty | AuxPoW flag enforces merged-mining validation |
| User-supplied optional overrides protocol enforcement | Absent optional bypasses protocol enforcement |

---

### Impact Explanation

An attacker who submits a Dogecoin block with `BLOCK_VERSION_AUXPOW` set but `aux_data = None` causes the contract to:

- Accept a block that the real Dogecoin network would reject (AuxPoW flag present but no valid AuxPoW chain).
- Record this block in `headers_pool` and potentially promote it to the mainchain via `submit_block_header_inner`.
- Corrupt the canonical chain mapping (`mainchain_height_to_header`, `mainchain_header_to_height`, `mainchain_tip_blockhash`). [5](#0-4) 

Any downstream NEAR contract calling `verify_transaction_inclusion_v2` against this corrupted chain state would receive a `true` result for a transaction inclusion proof anchored to a block that does not exist on the real Dogecoin chain. [6](#0-5) 

---

### Likelihood Explanation

**Low.** To exploit this, the attacker must brute-force a nonce in the Dogecoin block header such that `scrypt(block_header) <= target_from_bits(bits)`. This is equivalent in work to mining a legitimate Dogecoin block at the current network difficulty. Dogecoin's difficulty is substantially lower than Bitcoin's, but still requires dedicated hardware. The attack is realistic only for a well-resourced adversary (e.g., a miner with significant Dogecoin hashrate) who wishes to inject a phantom block into the contract's chain view without performing valid merged mining.

---

### Recommendation

In `submit_block_header` (Dogecoin build), before taking the non-AuxPoW fallback path, assert that the block does not carry the AuxPoW version flag:

```rust
if let Some(ref aux_data) = aux_data {
    self.check_aux(&block_header, aux_data);
} else {
    // Analog fix: enforce that AuxPoW-flagged blocks must supply aux_data
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "AuxPoW flag is set but no AuxPoW data was provided"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
}
``` [1](#0-0) 

---

### Proof of Concept

1. Observe that `BLOCK_VERSION_AUXPOW = 0x100` is defined locally in `check_aux` but is never consulted in the `aux_data = None` branch.
2. Craft a Dogecoin block header with `version = 0x100` (AuxPoW flag set), `prev_block_hash` pointing to the current contract chain tip, and `bits` matching the expected target.
3. Brute-force `nonce` until `scrypt(block_header) <= target_from_bits(bits)` — this is standard Dogecoin mining work.
4. Call `submit_blocks` on the NEAR contract with `headers = [(block_header, None)]`.
5. The contract executes `check_target` (passes — bits are correct) and then the `else` branch (passes — own hash satisfies target). `check_aux` is never called.
6. The block is stored in `headers_pool` and recorded in the mainchain maps.
7. A subsequent call to `verify_transaction_inclusion_v2` with a fabricated merkle proof against this block's `merkle_root` returns `true`, falsely attesting to a transaction inclusion that has no basis on the real Dogecoin chain. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/dogecoin.rs (L49-76)
```rust
    pub(crate) fn check_aux(&mut self, block_header: &Header, aux_data: &AuxData) {
        // The Dogecoin block must have the AuxPoW flag set (bit 8) when AuxPoW data is present.
        // https://github.com/dogecoin/dogecoin/blob/master/src/auxpow.h
        const BLOCK_VERSION_AUXPOW: i32 = 0x100;

        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );

        let chain_id = self.get_config().aux_chain_id;

        require!(
            chain_id == block_header.get_chain_id(),
            format!(
                "block does not have our chain ID (got {}, expected {chain_id})",
                block_header.get_chain_id()
            )
        );

        require!(
            chain_id != aux_data.parent_block.get_chain_id(),
            "Aux POW parent has our chain ID"
        );
```

**File:** contract/src/dogecoin.rs (L149-154)
```rust
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
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
