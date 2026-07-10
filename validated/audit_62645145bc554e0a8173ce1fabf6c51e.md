### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function has no access restriction and is callable by any unprivileged NEAR account. It does not validate that the supplied `tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node. An attacker can forge a proof of transaction inclusion by supplying a crafted internal-node hash, causing the function to return `true` for a transaction that was never included in the block.

---

### Finding Description

`verify_transaction_inclusion` was deprecated at v0.5.0 specifically because of the 64-byte transaction Merkle proof forgery vulnerability. The deprecation notice and the inline `# Warning` in the docstring both acknowledge the flaw:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

Despite this, the function remains a fully public, unrestricted contract method. Its only guards are:

1. A `#[pause]` attribute (pausable by a `PauseManager`, but not paused by default).
2. A non-empty `merkle_proof` check at line 315. [1](#0-0) 

Neither guard prevents an unprivileged caller from supplying an internal Merkle tree node as `tx_id`. The verification logic in `compute_root_from_merkle_proof` simply hashes the supplied `tx_id` up the tree: [2](#0-1) 

Because internal nodes are also 32-byte double-SHA256 hashes, an attacker who knows the Merkle tree structure of a real block can pick any internal node `N` at depth `d`, supply a proof path of length `d` from `N` to the root, and have `compute_root_from_merkle_proof` return the correct `merkle_root`. The function then returns `true`. [3](#0-2) 

The v2 function (`verify_transaction_inclusion_v2`) was introduced to close this gap by requiring a coinbase proof at index 0, which anchors the proof to a real leaf: [4](#0-3) 

However, v1 remains callable directly, bypassing the coinbase check entirely. The `From<ProofArgsV2> for ProofArgs` conversion confirms that v1 is the underlying engine and that its `tx_id` field is passed through without any leaf-validity check: [5](#0-4) 

**Analog to the external report:** In the wAlgo report, the minter's delegate TEAL script does not check that the asset-transfer sender is not the minter itself, allowing the minter to self-transfer and bypass the burn payment. Here, `verify_transaction_inclusion` does not check that `tx_id` is a leaf-level transaction hash rather than an internal node, allowing any caller to self-supply a crafted internal hash and bypass the proof-verification guarantee. Both are missing validation checks on a critical operation that allow an attacker to obtain a security-positive result without satisfying the underlying requirement.

---

### Impact Explanation

Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion` (v1) to gate asset releases, cross-chain settlements, or other security-sensitive decisions can be deceived into accepting a forged proof. The attacker receives a `true` result for a transaction that was never included in the block, enabling fraudulent cross-chain claims.

---

### Likelihood Explanation

**Medium-High.** The function is publicly callable with no role restriction. The 64-byte Merkle proof forgery attack is well-documented (referenced in the contract's own deprecation notice and at https://www.bitmex.com/blog/64-Byte-Transactions). All required inputs — a valid mainchain block hash, an internal node hash from that block's Merkle tree, and a crafted proof path — are attacker-controllable from public Bitcoin chain data. No privileged access, leaked keys, or social engineering is required.

---

### Recommendation

- **Short term:** Add `require!(false, "verify_transaction_inclusion is deprecated and disabled; use verify_transaction_inclusion_v2")` as the first statement in `verify_transaction_inclusion`, or mark it `#[private]` to prevent external calls entirely.
- **Long term:** Remove `verify_transaction_inclusion` from the public ABI in the next major version. Document the migration path clearly and add a negative integration test that confirms direct calls to v1 are rejected.

---

### Proof of Concept

1. Identify a block `B` in the contract's mainchain whose Merkle tree is known (e.g., a Bitcoin mainnet block with publicly available transaction data).
2. Compute an internal node `N` at depth `d` in `B`'s Merkle tree (e.g., the parent of two leaf transactions).
3. Construct a proof path `P` of length `d` from `N` upward to `merkle_root` (each element is the sibling hash at that level).
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash = B`
   - `tx_index` = the position of `N` at depth `d` (even or odd, matching the sibling direction)
   - `merkle_proof = P`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(N, tx_index, P)` traverses `d` steps and produces `merkle_root`.
6. The function returns `true`, falsely asserting that `N` is an included transaction in block `B`. [6](#0-5) [2](#0-1)

### Citations

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
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

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
