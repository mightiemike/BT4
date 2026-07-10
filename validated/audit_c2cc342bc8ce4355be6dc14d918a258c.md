### Title
`verify_transaction_inclusion` Accepts Internal Merkle Tree Node Hashes as Valid Transaction IDs, Enabling Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated but still-callable `verify_transaction_inclusion` function performs no validation that the supplied `tx_id` is a leaf node (a real transaction hash) in the Bitcoin Merkle tree. Any unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id` with a shortened proof path and receive `true` from the function, forging transaction inclusion for a transaction that was never broadcast or mined.

---

### Finding Description

`verify_transaction_inclusion` accepts caller-supplied `tx_id`, `tx_index`, `merkle_proof`, and `tx_block_blockhash`. Its only Merkle-level check is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no awareness of tree depth or leaf vs. internal node distinction:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;
    for proof_hash in merkle_proof {
        ...
        current_position /= 2;
    }
    current_hash
}
``` [2](#0-1) 

Because Bitcoin's Merkle tree does not domain-separate leaf nodes from internal nodes, an internal node `N = double_sha256(A || B)` is indistinguishable from a leaf hash at the computation level. An attacker who knows any real block's Merkle tree can supply `tx_id = N` (an internal node at depth `d`) with a proof of length `(tree_depth - d)` and receive `true`.

The contract's own documentation acknowledges this broken invariant:

> **Warning**: This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [3](#0-2) 

Despite being marked `#[deprecated]`, the function remains a live public entry point: [4](#0-3) 

`verify_transaction_inclusion_v2` fixes this by requiring `merkle_proof.len() == coinbase_merkle_proof.len()` and anchoring the proof depth to a verified coinbase transaction, but `v1` is never gated, removed, or restricted to privileged callers. [5](#0-4) 

---

### Impact Explanation

This is a **proof-verification forgery** vulnerability. The corrupted proof result is: `verify_transaction_inclusion` returns `true` for a `tx_id` that does not correspond to any real Bitcoin transaction. Any downstream bridge or peg-in contract that calls `verify_transaction_inclusion` to gate a privileged action (e.g., minting wrapped BTC, releasing collateral, crediting a user account) can be made to authorize that action for a fabricated transaction. The attacker does not need to broadcast any Bitcoin transaction; they only need to identify an existing mined block and compute an internal Merkle node from its public transaction list.

---

### Likelihood Explanation

- The function is publicly callable by any unprivileged NEAR account with no role check beyond the `#[pause]` flag (which is an operational control, not a security gate).
- All Bitcoin block Merkle trees are public; computing internal node hashes requires only standard `double_sha256` over known transaction IDs.
- The attack is deterministic and requires no brute force.
- The vulnerability class (64-byte / CVE-2012-2459 style Merkle forgery) is well-documented and tooling exists to exploit it.

---

### Recommendation

1. **Remove `verify_transaction_inclusion` (v1) entirely** from the public ABI. It is deprecated and its known-broken invariant is documented in the source. Keeping it callable means any integrator who has not migrated is silently exposed.
2. If removal is not immediately possible, add an explicit `env::panic_str` or `require!(false, "use v2")` body so calls revert rather than return a forgeable boolean.
3. Ensure all downstream consumers of this contract's verification API are audited to confirm they call `verify_transaction_inclusion_v2` exclusively.

---

### Proof of Concept

Take any real Bitcoin mainnet block with ≥ 2 transactions, e.g., block at height 685452 (already used in the contract's own test suite).

**Setup** — block has transactions `[tx0, tx1, tx2, tx3, ...]` with Merkle tree:

```
Level 0 (leaves): tx0,        tx1,        tx2,        tx3
Level 1:          N01=H(tx0||tx1),        N23=H(tx2||tx3)
Level 2 (root):   root=H(N01||N23)
```

**Forge** — attacker computes `N01 = double_sha256(tx0_bytes || tx1_bytes)` (public data).

**Call** `verify_transaction_inclusion` with:
- `tx_id` = `N01` (internal node, not a real transaction)
- `tx_block_blockhash` = block hash of the chosen block (already in the contract's mainchain)
- `tx_index` = `0` (position of `N01` at level 1)
- `merkle_proof` = `[N23]` (length 1, one level shorter than the real leaf proof)
- `confirmations` = `1`

**Execution** inside `compute_root_from_merkle_proof`:
- `current_hash = N01`, `current_position = 0`
- Iteration 1: position `0` is even → `current_hash = H(N01 || N23) = root` ✓

The function returns `true`. The contract has certified that `N01` — a value that is not a Bitcoin transaction — was included in the block. A bridge contract consuming this result would proceed to mint or release funds for a transaction that never existed. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
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
