### Title
AuxPoW-Flagged Dogecoin Blocks Accepted Without AuxPoW Validation — (`contract/src/dogecoin.rs`)

### Summary

The Dogecoin `submit_block_header` function handles two block variants: AuxPoW blocks (when `aux_data` is `Some`) and standard Scrypt-PoW blocks (when `aux_data` is `None`). The `else` branch for the `None` case never checks whether the submitted block header has the AuxPoW version flag set. A block carrying the AuxPoW flag but submitted without `aux_data` silently falls through to a plain Scrypt hash check, bypassing all AuxPoW validation.

### Finding Description

In `contract/src/dogecoin.rs`, `submit_block_header` dispatches on the presence of `aux_data`: [1](#0-0) 

When `aux_data` is `Some`, `check_aux` is called, which enforces the AuxPoW flag requirement: [2](#0-1) 

When `aux_data` is `None`, the code falls back to checking the block's own Scrypt hash against the target: [3](#0-2) 

There is no corresponding guard in the `else` branch to reject a block whose `version & BLOCK_VERSION_AUXPOW != 0`. The missing check is:

```rust
require!(
    block_header.version & BLOCK_VERSION_AUXPOW == 0,
    "AuxPoW block submitted without AuxPoW data"
);
```

The `BlockHeader` type for the Dogecoin build is `(Header, Option<AuxData>)`, so any caller of `submit_blocks` can freely omit the `AuxData` field: [4](#0-3) 

`block_hash_pow()` for the Dogecoin/Litecoin build computes a Scrypt hash over the raw 80-byte header (including the `version` field with the AuxPoW flag): [5](#0-4) 

### Impact Explanation

An attacker who can act as a registered relayer submits a crafted Dogecoin block header with `version & 0x100 != 0` (AuxPoW flag set) and `aux_data = None`. The contract validates it purely via Scrypt PoW on the block's own header bytes. If the attacker mines a nonce satisfying the Scrypt target, the contract stores the block as canonical even though it is invalid on the real Dogecoin network (Dogecoin mandates AuxPoW for all blocks with the AuxPoW flag after height 371337). This corrupts `mainchain_height_to_header` and `mainchain_tip_blockhash`, causing `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to return `true` for transactions in attacker-controlled fake blocks. [6](#0-5) 

### Likelihood Explanation

The entry point is `submit_blocks`, which is gated by the `#[trusted_relayer]` macro — requiring the caller to stake NEAR tokens, not a privileged key. Any party willing to stake can become a relayer. Dogecoin's Scrypt difficulty is orders of magnitude lower than Bitcoin's SHA-256d difficulty; a well-resourced attacker with Scrypt ASIC capacity (e.g., a Litecoin mining pool, which shares the same algorithm) can mine a valid-looking header at current Dogecoin difficulty. The attack is therefore realistic for a motivated, moderately-resourced adversary.

### Recommendation

Add an explicit guard in the `else` branch of `submit_block_header` to reject any block whose version carries the AuxPoW flag when no `AuxData` is provided:

```rust
} else {
    require!(
        block_header.version & BLOCK_VERSION_AUXPOW == 0,
        "AuxPoW block submitted without AuxPoW data"
    );
    let pow_hash = block_header.block_hash_pow();
    require!(
        U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
        "block should have correct pow"
    );
}
```

This mirrors the symmetric check already present inside `check_aux` (which rejects AuxPoW-data submissions for non-AuxPoW-flagged blocks) and closes the incomplete variant handling.

### Proof of Concept

1. Deploy the contract with the `dogecoin` feature flag against Dogecoin mainnet.
2. Register as a trusted relayer by staking the required NEAR deposit.
3. Construct a `(Header, Option<AuxData>)` where:
   - `header.version = 0x00620100` (chain-id `0x0062`, AuxPoW flag `0x100` set)
   - `header.prev_block_hash` = current mainchain tip hash
   - `header.bits` = value returned by `get_next_work_required` for the next block
   - `aux_data = None`
4. Mine `header.nonce` until `scrypt(header_bytes) <= target_from_bits(header.bits)`.
5. Call `submit_blocks([constructed_header])`.
6. The contract accepts the block via the `else` branch (Scrypt check passes, AuxPoW flag never checked), stores it as the new mainchain tip, and will subsequently return `true` from `verify_transaction_inclusion` for any transaction whose hash is placed in `header.merkle_root`. [7](#0-6)

### Citations

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

**File:** btc-types/src/header.rs (L19-20)
```rust
#[cfg(feature = "dogecoin_header")]
pub type BlockHeader = (Header, Option<AuxData>);
```

**File:** btc-types/src/btc_header.rs (L38-53)
```rust
    pub fn block_hash_pow(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        #[cfg(feature = "scrypt_hash")]
        {
            let params = scrypt::Params::new(10, 1, 1, 32).unwrap(); // N=1024 (2^10), r=1, p=1

            let mut output = [0u8; 32];
            scrypt::scrypt(&block_header, &block_header, &params, &mut output).unwrap();
            H256::from(output)
        }

        #[cfg(not(feature = "scrypt_hash"))]
        {
            double_sha256(&block_header)
        }
    }
```

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
