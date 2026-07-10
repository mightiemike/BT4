### Title
Deprecated `verify_transaction_inclusion` (v1) Remains Publicly Callable, Enabling 64-Byte Transaction Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function (v1) is still a live, publicly callable NEAR contract method. Rust's `#[deprecated]` attribute is a compile-time lint only — it does not gate runtime invocation. V1 omits the coinbase Merkle proof check that was introduced in v2 specifically to close the 64-byte transaction Merkle forgery vector. An unprivileged NEAR caller can invoke v1 directly, supply an internal Merkle tree node as `tx_id`, and receive a `true` verification result for a Bitcoin transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` (v1) is declared public and carries only `#[deprecated]` and `#[pause]` attributes: [1](#0-0) 

The function's own NatSpec warns that it may return `true` for internal Merkle tree nodes: [2](#0-1) 

The entire verification body is a single Merkle root comparison with no leaf-node guard: [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools` accepts any 32-byte value as the starting hash and walks the proof path without any constraint that the input must be a transaction leaf: [4](#0-3) 

`verify_transaction_inclusion_v2` closes this gap by first asserting that a coinbase proof at index 0 matches the block's Merkle root before delegating to v1: [5](#0-4) 

Because v1 is still a public entry point, a caller can bypass v2's coinbase guard entirely by calling v1 directly. The 64-byte transaction attack works as follows: Bitcoin's Merkle tree hashes pairs of 32-byte child hashes (64 bytes) to produce each internal node. A crafted 64-byte blob can be interpreted as two 32-byte hashes whose `double_sha256` equals a real internal node. The attacker supplies that internal node hash as `tx_id`, picks a `tx_index` and `merkle_proof` that reproduce the block's stored `merkle_root`, and v1 returns `true`.

---

### Impact Explanation

Any recipient contract that calls `verify_transaction_inclusion` (v1) to gate a cross-chain action — releasing funds, minting wrapped tokens, recording a settlement — will accept a forged proof as valid. The corrupted proof result is `true` for a Bitcoin transaction that was never broadcast or confirmed. This enables an attacker to claim an on-chain NEAR benefit backed by a fabricated Bitcoin payment.

The exact corrupted value is the boolean return of `verify_transaction_inclusion`, which transitions from `false` (correct) to `true` (forged) for any `tx_id` that is a valid internal Merkle node of a real mainchain block.

---

### Likelihood Explanation

**Medium.** The entry path requires no privileged role, no leaked key, and no social engineering. The attacker needs only: (1) a block hash present in the contract's mainchain (publicly readable via `get_block_hash_by_height`), (2) knowledge of that block's Merkle tree structure (available from any Bitcoin full node), and (3) the ability to call a NEAR contract. The 64-byte transaction technique is publicly documented (BitMEX research, referenced in the contract's own deprecation notice). The only friction is constructing the 64-byte preimage whose double-SHA256 equals a chosen internal node — a known, solved problem in the Bitcoin security literature.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public contract interface entirely. Because `#[deprecated]` does not prevent on-chain calls, the function must be deleted or replaced with a private helper. All callers should be migrated to `verify_transaction_inclusion_v2`. If backward compatibility is required for an interim period, gate v1 behind an access-control role so unprivileged callers cannot reach it.

---

### Proof of Concept

1. Query `get_block_hash_by_height(H)` to obtain a real mainchain `block_hash` and retrieve that block's full transaction list from a Bitcoin node.
2. Identify an internal Merkle node `N` at depth `d` in the block's Merkle tree. Construct a 64-byte blob whose `double_sha256` equals `N` (standard 64-byte transaction technique).
3. Build a `merkle_proof` of length `d` that, starting from `N` as `tx_id` at the appropriate `tx_index`, reproduces the block's `merkle_root` via `compute_root_from_merkle_proof`.
4. Call `verify_transaction_inclusion` (v1) with `tx_id = N`, `tx_block_blockhash = block_hash`, `tx_index`, `merkle_proof`, `confirmations = 1`.
5. The function returns `true`. The same call to `verify_transaction_inclusion_v2` would panic at the coinbase proof assertion, confirming that v1's missing guard is the sole root cause. [6](#0-5) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

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
