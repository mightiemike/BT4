### Title
AuxPoW Chain Merkle Root Byte-Order Mismatch Causes Permanent Rejection of All Valid Dogecoin AuxPoW Blocks - (File: `contract/src/dogecoin.rs`)

---

### Summary

In `check_aux()`, the computed chain merkle root is searched inside the coinbase `script_sig` using `chain_root.to_string()`. The `Display` implementation of `H256` **reverses** the bytes (producing big-endian / display-order hex), but the chain merkle root is embedded in the coinbase script in **internal byte order** (little-endian, as Bitcoin/Dogecoin stores all hashes on the wire). The two representations never match, so the `.find()` call always panics, making it impossible to submit any valid AuxPoW Dogecoin block.

---

### Finding Description

**Root cause — `H256::Display` reverses bytes:** [1](#0-0) 

```rust
impl fmt::Display for H256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reversed: Vec<u8> = self.0.into_iter().rev().collect();
        write!(f, "{}", hex::encode(reversed))
    }
}
```

`H256` stores hashes in internal byte order (the natural SHA-256 output, little-endian as used in Bitcoin wire format). `Display` reverses those bytes to produce the human-readable big-endian hex shown in block explorers.

**Broken search in `check_aux()`:** [2](#0-1) 

```rust
let chain_root = merkle_tools::compute_root_from_merkle_proof(
    block_header.block_hash(),
    aux_data.chain_id,
    &aux_data.chain_merkle_proof,
);
// ...
let script_sig = coinbase_tx
    .input.first().unwrap()
    .script_sig.to_hex_string();          // raw bytes → hex, internal byte order

let mut pos_chain_root = script_sig
    .find(&chain_root.to_string())        // ← reversed (big-endian) hex
    .expect("Aux POW missing chain merkle root in parent coinbase");
```

`script_sig.to_hex_string()` (from the `bitcoin` crate) hex-encodes the raw script bytes. The chain merkle root is embedded in those bytes in **internal byte order** (little-endian). `chain_root.to_string()` produces the **reversed** (big-endian) hex. The two 64-character strings are mirror images of each other and will never match for any real hash.

**Contrast with the correct `MERGED_MINING_HEADER` search:** [3](#0-2) 

The constant `"fabe6d6d"` is a literal byte sequence with no endianness ambiguity, so that search works correctly. Only the 32-byte hash comparison is broken.

---

### Impact Explanation

Every valid AuxPoW Dogecoin block submitted via `submit_blocks()` will hit the `.expect("Aux POW missing chain merkle root in parent coinbase")` panic inside `check_aux()`. [4](#0-3) 

Because Dogecoin is merge-mined with Bitcoin, the overwhelming majority of mainnet Dogecoin blocks carry AuxPoW data (`aux_data = Some(...)`). The contract's Dogecoin light client is therefore **permanently non-functional**: it cannot advance its chain tip, cannot record new headers

### Citations

**File:** btc-types/src/hash.rs (L19-24)
```rust
impl fmt::Display for H256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reversed: Vec<u8> = self.0.into_iter().rev().collect();
        write!(f, "{}", hex::encode(reversed))
    }
}
```

**File:** contract/src/dogecoin.rs (L11-11)
```rust
const MERGED_MINING_HEADER: &str = "fabe6d6d";
```

**File:** contract/src/dogecoin.rs (L78-104)
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
