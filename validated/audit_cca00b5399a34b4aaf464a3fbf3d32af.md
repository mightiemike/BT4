### Title
Missing AuxPoW Type Consistency Validation Allows AuxPoW Flag Bypass — (`contract/src/dogecoin.rs`)

### Summary

In the Dogecoin build of the BTC light client, `submit_block_header` branches on whether the caller-supplied `aux_data` is `Some` or `None` to decide which PoW path to execute. It never validates that the block header's `BLOCK_VERSION_AUXPOW` version flag is consistent with the presence or absence of `aux_data`. A malicious trusted relayer can submit a block whose version field declares it as an AuxPoW block (`BLOCK_VERSION_AUXPOW` set) while supplying `aux_data = None`, causing the contract to execute only the standard PoW path and skip all AuxPoW-specific validation.

### Finding Description

`BlockHeader` for the Dogecoin build is the tuple type `(Header, Option<AuxData>)`. [1](#0-0) 

The `submit_block_header` function branches exclusively on whether `aux_data` is `Some` or `None`: [2](#0-1) 

When `aux_data` is `None`, only the standard PoW hash of the Dogecoin block itself is checked against the target. There is no assertion that `block_header.version & BLOCK_VERSION_AUXPOW == 0`.

The `check_aux` function does validate the inverse direction — it rejects an `aux_data`-present call if the version flag is absent: [3](#0-2) 

But the symmetric guard — rejecting a `None`-aux_data call when the version flag is present — is entirely missing. The two type discriminants (`version & BLOCK_VERSION_AUXPOW` and `Option<AuxData>`) are never cross-validated when `aux_data` is `None`.

### Impact Explanation

An attacker who mines a Dogecoin block with `BLOCK_VERSION_AUXPOW` set in the version field and whose own block hash meets the standard PoW target can submit it with `aux_data = None`. The contract will:

1. Accept the block through the standard PoW branch.
2. Skip the entire `check_aux` path, bypassing: chain-ID validation, coinbase transaction parsing and merkle proof verification, parent block PoW validation, and the merged-mining header position checks. [4](#0-3) 

The accepted block is then stored in `headers_pool` and can become the canonical chain tip via `submit_block_header_inner`, corrupting `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height`. [5](#0-4) 

Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive results anchored to a fraudulently accepted block.

### Likelihood Explanation

Becoming a trusted relayer is permissionless — any account that stakes the required NEAR tokens qualifies. [6](#0-5) 

The attacker must mine a Dogecoin block with `BLOCK_VERSION_AUXPOW` set whose own hash meets the current Dogecoin PoW target. This is standard Dogecoin mining with one version bit forced; it requires real hashrate but no privileged access. A well-resourced attacker (e.g., a merge-miner already operating on the Dogecoin network) can produce such a block without extraordinary effort.

### Recommendation

In `submit_block_header` (Dogecoin build), after destructuring the tuple, add an explicit consistency check between the version flag and the presence of `aux_data`:

```rust
let (block_header, aux_data) = header;

// Validate type consistency: BLOCK_VERSION_AUXPOW flag must match aux_data presence
const BLOCK_VERSION_AUXPOW: i32 = 0x100;
let is_auxpow_version = block_header.version & BLOCK_VERSION_AUXPOW != 0;
require!(
    is_auxpow_version == aux_data.is_some(),
    "AuxPoW version flag must match presence of aux_data"
);
```

This mirrors the fix described in the reference report: validate the type discriminant against the expected variant before branching on it.

### Proof of Concept

1. Stake NEAR to register as a trusted relayer on the Dogecoin deployment.
2. Construct a `Header` with `version = 0x100` (`BLOCK_VERSION_AUXPOW` set) and any valid `prev_block_hash`, `merkle_root`, `time`, and `bits`.
3. Mine a `nonce` such that `double_sha256(header_bytes)` meets `target_from_bits(bits)` — standard PoW mining with the version bit forced.
4. Call `submit_blocks` with `[(header, None)]` (Borsh-serialized).
5. The contract executes the `else` branch at line 182–188 of `dogecoin.rs`, passes the standard PoW check, and stores the block via `submit_block_header_inner`.
6. All AuxPoW-specific checks (chain ID, coinbase merkle proof, parent block PoW) are never executed.
7. The block becomes part of the canonical chain state; `verify_transaction_inclusion` results are now anchored to a fraudulently accepted block. [7](#0-6)

### Citations

**File:** btc-types/src/header.rs (L19-20)
```rust
#[cfg(feature = "dogecoin_header")]
pub type BlockHeader = (Header, Option<AuxData>);
```

**File:** contract/src/dogecoin.rs (L54-61)
```rust
        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );
```

**File:** contract/src/dogecoin.rs (L78-154)
```rust
        let chain_root = merkle_tools::compute_root_from_merkle_proof(
            block_header.block_hash(),
            aux_data.chain_id,
            &aux_data.chain_merkle_proof,
        );

        let coinbase_tx = aux_data.get_coinbase_tx();
        let coinbase_tx_hash = coinbase_tx.compute_txid();

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
                0,
                &aux_data.merkle_proof,
            ) == aux_data.parent_block.merkle_root
        );

        let script_sig = coinbase_tx
            .input
            .first()
            .unwrap()
            .script_sig
            .to_hex_string();
        let pos_merged_mining_header = script_sig.find(MERGED_MINING_HEADER);
        let mut pos_chain_root = script_sig
            .find(&chain_root.to_string())
            .expect("Aux POW missing chain merkle root in parent coinbase");

        match pos_merged_mining_header {
            Some(pos_merged_mining_header) => {
                if script_sig[pos_merged_mining_header + MERGED_MINING_HEADER.len()..]
                    .contains(MERGED_MINING_HEADER)
                {
                    env::panic_str("Multiple merged mining headers in coinbase");
                }

                require!(
                    pos_merged_mining_header + MERGED_MINING_HEADER.len() == pos_chain_root,
                    "Merged mining header is not just before chain merkle root"
                );
            }
            None => {
                require!(pos_chain_root <= 40, "Aux POW chain merkle root must start in the first 20 bytes of the parent coinbase");
            }
        }

        pos_chain_root += chain_root.to_string().len();
        require!(
            script_sig.len() - pos_chain_root >= 16,
            "Aux POW missing chain merkle tree size and nonce in parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root..pos_chain_root + 8]).unwrap();
        let n_size = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        require!(
            n_size == (1u32 << aux_data.chain_merkle_proof.len()),
            "Aux POW merkle branch size does not match parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root + 8..pos_chain_root + 16]).unwrap();
        let n_nonce = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);

        let chain_id = block_header.get_chain_id();

        let expected_index =
            Self::get_expected_index(n_nonce, chain_id, aux_data.chain_merkle_proof.len());

        require!(
            u32::try_from(aux_data.chain_id).ok() == Some(expected_index),
            "Aux POW wrong index"
        );
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

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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
