### Title
Synthetic Zero-Filled AuxPoW Chain Merkle Proof Bypasses Merged Mining Validation — (`contract/src/dogecoin.rs`, `check_aux`)

---

### Summary

`check_aux` never validates that the hashes in `aux_data.chain_merkle_proof` are non-zero or represent real sibling nodes from a live merged mining tree. Because the attacker controls every field of `AuxData` — including `coinbase_tx`, `merkle_proof`, `chain_id`, `chain_merkle_proof`, and `parent_block` — they can construct a fully synthetic AuxPoW proof with zero-filled chain merkle siblings that passes every check in `check_aux`, provided they mine a parent block whose scrypt PoW hash satisfies the Dogecoin target.

---

### Finding Description

**Entrypoint**: Any caller can invoke `submit_blocks()` → `submit_block_header()` → `check_aux()`. The `AuxData` struct is entirely attacker-supplied with no trusted-relayer restriction on its contents.

**Trace through `check_aux`** (`contract/src/dogecoin.rs` lines 49–155):

**Guard 1 — chain_root computation** (lines 78–82):

```rust
let chain_root = merkle_tools::compute_root_from_merkle_proof(
    block_header.block_hash(),
    aux_data.chain_id,
    &aux_data.chain_merkle_proof,
);
```

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` (lines 34–52) iterates over `merkle_proof` hashes and calls `double_sha256` on concatenated pairs. There is **no check that any proof hash is non-zero**. With `chain_merkle_proof = [H256::zero(); N]`, the result is a deterministic, pre-computable `chain_root` that depends only on `block_hash` and `chain_id`. [1](#0-0) [2](#0-1) 

**Guard 2 — coinbase tx hash vs. parent_block.merkle_root** (lines 84–93):

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
        0,
        &aux_data.merkle_proof,
    ) == aux_data.parent_block.merkle_root
);
```

The attacker controls `coinbase_tx`, `merkle_proof`, and `parent_block.merkle_root`. With an empty `merkle_proof`, `compute_root_from_merkle_proof` returns `coinbase_tx_hash` directly. Setting `parent_block.merkle_root = coinbase_tx_hash` trivially satisfies this check. [3](#0-2) 

**Guard 3 — chain_root in coinbase script** (lines 95–104):

The attacker controls the coinbase script content, so they embed the pre-computed `chain_root` directly. [4](#0-3) 

**Guard 4 — n_size check** (lines 130–135):

```rust
require!(n_size == (1u32 << aux_data.chain_merkle_proof.len()));
```

The attacker controls both `n_size` (embedded in coinbase script) and `chain_merkle_proof.len()`. Trivially satisfiable. [5](#0-4) 

**Guard 5 — expected index check** (lines 137–148):

```rust
let expected_index =
    Self::get_expected_index(n_nonce, chain_id, aux_data.chain_merkle_proof.len());
require!(u32::try_from(aux_data.chain_id).ok() == Some(expected_index));
```

`get_expected_index` is a simple LCG over a u32 nonce: [6](#0-5) 

The attacker controls `n_nonce` (via coinbase script), `chain_merkle_proof.len()`, and `aux_data.chain_id`. They iterate over all 2^32 values of `n_nonce` to find one where `get_expected_index(n_nonce, doge_chain_id, N) == chosen_chain_id`. This is trivially feasible offline. Changing `n_nonce` changes the coinbase script, which changes `coinbase_tx_hash`, but the attacker also controls `parent_block.merkle_root`, so they update it accordingly.

**Guard 6 — parent block PoW** (lines 149–154):

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
);
```

`block_hash_pow()` uses scrypt when the `scrypt_hash` feature is enabled (as it is for Dogecoin): [7](#0-6) 

The attacker controls all fields of `parent_block` (version, prev_block_hash, merkle_root, time, bits, nonce). They set `parent_block.merkle_root` to the coinbase tx hash and mine the parent block by iterating `nonce` until the scrypt hash satisfies the Dogecoin target. The Dogecoin mainnet target (e.g., `0x1a00a52e` from test data) is far easier than Bitcoin's target, making this feasible with commodity scrypt mining hardware. [8](#0-7) 

---

### Impact Explanation

An attacker can submit a Dogecoin block header with a fully synthetic AuxPoW proof — zero-filled chain merkle siblings, a crafted coinbase, and a self-mined parent block — that passes all checks in `check_aux`. The contract accepts the block as validly merged-mined when it was never committed to by any real parent chain miner. This breaks the invariant that the chain merkle branch must commit the Dogecoin block to a specific position in a real merged mining tree with real sibling hashes, enabling **false canonical header acceptance**.

---

### Likelihood Explanation

The only real cost is mining a scrypt block satisfying the Dogecoin target. Dogecoin's target is significantly easier than Bitcoin's (Dogecoin mainnet bits are typically in the `0x1a`–`0x1b` range). An attacker with access to commodity Litecoin/Dogecoin scrypt mining hardware can produce such a block. All other steps (computing `chain_root`, finding `n_nonce`, crafting the coinbase) are pure offline computation with no cryptographic barrier.

---

### Recommendation

1. **Reject zero-valued proof hashes**: Add a check in `check_aux` that no element of `chain_merkle_proof` is `H256::zero()`.
2. **Validate parent block chain ID**: Verify that `parent_block.get_chain_id()` corresponds to a known merged mining parent chain (e.g., Litecoin chain ID), not an arbitrary value.
3. **Validate parent block bits**: Require that `parent_block.bits` reflects a real parent chain difficulty, not an artificially easy target.
4. **Validate merkle_proof is non-empty**: A real merged mining coinbase is always the first transaction in a block with other transactions; an empty `merkle_proof` (implying the coinbase is the only transaction) should be rejected or at minimum flagged.

---

### Proof of Concept

```
Given: Dogecoin block header B with block_hash = H, chain_id = 0x0062, bits = T

1. Choose N = 5, chain_merkle_proof = [H256::zero(); 5]
2. Choose aux_chain_id = P (any value in [0, 2^5))
3. chain_root = compute_root_from_merkle_proof(H, P, [zero; 5])  // deterministic
4. Iterate n_nonce over [0, 2^32):
     if get_expected_index(n_nonce, 0x0062, 5) == P: break
   // LCG, solution found in O(2^32) trivially
5. Craft coinbase_tx with script_sig:
     "fabe6d6d" || chain_root || LE32(2^5) || LE32(n_nonce)
6. coinbase_tx_hash = coinbase_tx.compute_txid()
7. Set parent_block.merkle_root = coinbase_tx_hash, merkle_proof = []
8. Mine parent_block: iterate parent_block.nonce until
     scrypt(parent_block_bytes) <= target_from_bits(T)
   // Dogecoin target is easy; feasible with commodity hardware
9. Submit (B, AuxData { coinbase_tx, merkle_proof: [], chain_merkle_proof: [zero;5],
                        chain_id: P, parent_block }) to submit_blocks()
→ check_aux passes all guards; block accepted as canonical
``` [9](#0-8)

### Citations

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```

**File:** contract/src/dogecoin.rs (L49-155)
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
    }
```

**File:** contract/src/dogecoin.rs (L157-164)
```rust
    fn get_expected_index(nonce: u32, chain_id: i32, merkle_height: usize) -> u32 {
        let mut rand = nonce;
        rand = rand.wrapping_mul(1_103_515_245).wrapping_add(12345);
        rand = rand.wrapping_add(u32::try_from(chain_id).unwrap());
        rand = rand.wrapping_mul(1_103_515_245).wrapping_add(12345);

        rand.wrapping_rem(1u32 << merkle_height)
    }
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

**File:** contract/tests/test_dogecoin.rs (L299-308)
```rust
            // Litecoin block 2935420 header (version=0x20000000, scrypt PoW, bits=0x19309265).
            parent_block: serde_json::from_value(json!({
                "version": 536870912i32,
                "prev_block_hash": "0d1db79cc92ff24f2657fb11b23e3c78c4a4743a9a77cb559bce5eae28344674",
                "merkle_root": "049c025705d4ceb6e3847712ebfa23762580fbc8b6915abaaa3243d1bc498d68",
                "time": 1753008193u32,
                "bits": 422613605u32,
                "nonce": 1225295933u32,
            }))
            .unwrap(),
```
