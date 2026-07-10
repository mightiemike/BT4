### Title
AuxPoW Chain Merkle Root Byte-Order Mismatch Causes Permanent Rejection of All Valid AuxPoW Dogecoin Blocks - (`contract/src/dogecoin.rs`)

### Summary

`check_aux` searches for the chain merkle root inside the parent coinbase script using `chain_root.to_string()`, which emits the **reversed (display/big-endian) hex** of the hash. The coinbase script stores the chain merkle root in **natural (little-endian / SHA256d output) byte order**. Because these two representations are always different for any real hash, the `.expect("Aux POW missing chain merkle root in parent coinbase")` call always panics, making it impossible to submit any valid AuxPoW Dogecoin block.

### Finding Description

In `contract/src/dogecoin.rs`, `check_aux` computes the chain merkle root and then searches for it inside the hex-encoded coinbase script:

```rust
let chain_root = merkle_tools::compute_root_from_merkle_proof(
    block_header.block_hash(),
    aux_data.chain_id,
    &aux_data.chain_merkle_proof,
);
// ...
let mut pos_chain_root = script_sig
    .find(&chain_root.to_string())          // ← reversed (display) hex
    .expect("Aux POW missing chain merkle root in parent coinbase");
```

`chain_root` is an `H256`, whose `Display` implementation **reverses** the 32 bytes before hex-encoding them:

```rust
// btc-types/src/hash.rs
impl fmt::Display for H256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reversed: Vec<u8> = self.0.into_iter().rev().collect();
        write!(f, "{}", hex::encode(reversed))
    }
}
```

`script_sig` is obtained via `coinbase_tx.input.first().unwrap().script_sig.to_hex_string()`, which encodes the raw script bytes in their natural order. The Dogecoin AuxPoW protocol stores the chain merkle root in the coinbase as a raw `uint256` in **natural (little-endian) byte order** — i.e., the direct SHA256d output bytes, not the reversed display form. Therefore `script_sig.find(&chain_root.to_string())` is searching for the wrong byte sequence and will never find a match in a legitimately constructed coinbase.

### Impact Explanation

Every valid Dogecoin AuxPoW block submission panics at the `.expect(...)` call inside `check_aux`. Because `submit_block_header` calls `check_aux` for every block that carries `AuxData`, **no valid AuxPoW Dogecoin block can ever be accepted by the contract**. This completely breaks the core functionality of the Dogecoin light client: the mainchain tip can never advance past the genesis window, and `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` will always fail for any block that requires AuxPoW.

### Likelihood Explanation

Dogecoin has used merged mining (AuxPoW) since block 371,337 (2014). Every Dogecoin mainnet block above that height carries AuxPoW data. Any relayer attempting to sync the Dogecoin light client past that height will trigger the panic on every single block. The bug is deterministic and 100% reproducible with any real Dogecoin AuxPoW block.

### Recommendation

Replace `chain_root.to_string()` with the natural-byte-order hex encoding so the search matches what is actually stored in the coinbase script:

```rust
// Before (wrong — reversed/display byte order):
let mut pos_chain_root = script_sig
    .find(&chain_root.to_string())
    .expect("Aux POW missing chain merkle root in parent coinbase");

// After (correct — natural/internal byte order):
let chain_root_hex = hex::encode(chain_root.0);
let mut pos_chain_root = script_sig
    .find(&chain_root_hex)
    .expect("Aux POW missing chain merkle root in parent coinbase");
```

### Proof of Concept

1. Take any real Dogecoin mainnet block above height 371,337 that carries AuxPoW.
2. Extract its `Header` and `AuxData` (coinbase tx, merkle proofs, chain id, parent block).
3. Call `submit_blocks` on the Dogecoin-feature build of the contract with this block.
4. Inside `check_aux`, `chain_root.to_string()` produces the reversed hex (e.g., `"abcd...1234"`) while the coinbase script contains the natural-order hex (e.g., `"3412...cdab"`).
5. `script_sig.find(...)` returns `None`; `.expect(...)` panics with `"Aux POW missing chain merkle root in parent coinbase"`.
6. The NEAR transaction aborts; the block is never stored; the mainchain tip does not advance.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contract/src/dogecoin.rs (L78-93)
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
```

**File:** contract/src/dogecoin.rs (L101-104)
```rust
        let pos_merged_mining_header = script_sig.find(MERGED_MINING_HEADER);
        let mut pos_chain_root = script_sig
            .find(&chain_root.to_string())
            .expect("Aux POW missing chain merkle root in parent coinbase");
```

**File:** btc-types/src/hash.rs (L19-24)
```rust
impl fmt::Display for H256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let reversed: Vec<u8> = self.0.into_iter().rev().collect();
        write!(f, "{}", hex::encode(reversed))
    }
}
```

**File:** btc-types/src/aux.rs (L18-21)
```rust
impl AuxData {
    pub fn get_coinbase_tx(&self) -> Transaction {
        deserialize(&self.coinbase_tx).unwrap()
    }
```
