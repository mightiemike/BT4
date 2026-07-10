### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Nodes as Valid Transaction IDs, Enabling Proof-Verification Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains publicly callable by any unprivileged NEAR account. It accepts a caller-supplied `tx_id` and verifies it against the block's Merkle root without confirming that `tx_id` is a leaf-level transaction hash. An attacker can supply a 64-byte internal Merkle tree node as `tx_id` with a structurally valid Merkle proof, causing the function to return `true` and falsely certify transaction inclusion. Consuming contracts that gate financial or authorization logic on this result are directly exploitable.

---

### Finding Description

`verify_transaction_inclusion` (lines 288–323) delegates proof checking entirely to `merkle_tools::compute_root_from_merkle_proof`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` treats `transaction_hash` as an opaque 32-byte value and hashes it upward through the proof path:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
``` [2](#0-1) 

No check distinguishes a leaf-level transaction hash from an internal node hash. The function's own inline warning acknowledges this:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [3](#0-2) 

Despite being marked `#[deprecated(since = "0.5.0")]`, the function is `pub` and decorated only with `#[pause]` (not paused by default). Rust's `#[deprecated]` attribute emits a compiler warning; it does not prevent on-chain invocation. Any NEAR account can call this method directly, bypassing the v2 path entirely. [4](#0-3) 

**Analog mapping to the ERC20 transfer-fee class:**

| Dimension | ERC20 transfer-fee bug | This repository |
|---|---|---|
| Stated value | `amount` passed by caller | `tx_id` passed by caller |
| Actual value | `amount − fee` actually received | internal Merkle node, not a real transaction |
| Contract assumption | full `amount` was received | `tx_id` is a leaf-level transaction hash |
| Broken invariant | supply/borrow balance overstated | `verify_transaction_inclusion` returns `true` for a forged proof |
| Root cause | no before/after balance check | no leaf-vs-internal-node distinction |

---

### Impact Explanation

Any NEAR smart contract that gates a security-critical action (releasing bridged funds, minting wrapped tokens, authorizing cross-chain messages) on the boolean result of `verify_transaction_inclusion` can be deceived. An attacker who can identify a 64-byte internal Merkle node in any confirmed Bitcoin block can present it as a "transaction" and receive a `true` result, fabricating proof of a BTC event that never occurred. The corrupted value is the **proof result** returned to the caller.

---

### Likelihood Explanation

The entry path requires no privileged role: `verify_transaction_inclusion` is a public, unpaused NEAR method callable by any account. The 64-byte forgery technique is publicly documented (referenced in the v2 deprecation notice itself). Finding a suitable internal node in a real Bitcoin block is computationally trivial — every block with more than one transaction contains internal nodes. The only additional requirement is that the consuming contract does not independently validate the raw transaction bytes, which is the common case for light-client consumers that rely on the contract's verification result.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely. If backward compatibility is required, replace the function body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` so that on-chain callers receive a hard failure rather than a potentially forged `true` result.

---

### Proof of Concept

1. Select any confirmed Bitcoin block stored in the contract's `headers_pool` that contains ≥ 2 transactions.
2. Reconstruct the block's Merkle tree. Pick any internal node `N` (a 32-byte hash produced by hashing two child hashes together). Record its position `p` in the tree and its sibling path (the Merkle proof).
3. Call `verify_transaction_inclusion` from any NEAR account:
   ```
   tx_id              = N          // internal node, not a real txid
   tx_block_blockhash = <block>
   tx_index           = p
   merkle_proof       = <sibling path for N>
   confirmations      = 1
   ```
4. `compute_root_from_merkle_proof(N, p, proof)` reconstructs the correct Merkle root because `N` is a genuine node in the tree.
5. The function returns `true`.
6. Any consuming contract that trusts this result now believes a Bitcoin transaction with id `N` was confirmed in that block, even though no such transaction exists. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
