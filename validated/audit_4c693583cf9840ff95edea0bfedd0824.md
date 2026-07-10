### Title
`verify_transaction_inclusion_v2` Coinbase Proof Does Not Bind to a Real Leaf, Allowing Internal-Node Forgery - (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` performs two independent Merkle-root checks — one for the "coinbase" and one for the target transaction — against the same `header.block_header.merkle_root`, with no aggregate constraint linking them. An unprivileged caller can supply the same internal Merkle-tree node as both `coinbase_tx_id` and `tx_id`, with the same proof, causing both checks to pass independently while the function returns `true` for a hash that is not a real transaction. This is the direct analog of the external report's pattern: two separate checks each pass against the same shared resource, with no combined validation.

### Finding Description

`verify_transaction_inclusion_v2` is the security-upgraded replacement for the deprecated v1 function, added specifically to mitigate the 64-byte transaction Merkle-proof forgery attack. Its mitigation strategy is:

1. Require `merkle_proof.len() == coinbase_merkle_proof.len()` (same tree depth).
2. Verify the coinbase proof against `header.block_header.merkle_root`.
3. Delegate to v1, which verifies the tx proof against the same `header.block_header.merkle_root`. [1](#0-0) 

The critical flaw: the contract never verifies that `coinbase_tx_id` is the actual coinbase transaction (a real leaf at index 0). It only checks that `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`. An attacker can supply any 32-byte value — including an internal Merkle node — as `coinbase_tx_id`. [2](#0-1) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no awareness of tree depth or leaf vs. internal-node distinction: [3](#0-2) 

The `ProofArgsV2 → ProofArgs` conversion simply drops the coinbase fields; the tx proof is then verified independently in v1 against the same root: [4](#0-3) [5](#0-4) 

**Concrete attack path:**

Let block B have a Merkle tree of depth N. Let X be the leftmost internal node at depth D (e.g., the parent of the coinbase and the second transaction). X has a valid Merkle proof P of length N−D that satisfies `compute_root_from_merkle_proof(X, 0, P) == merkle_root`.

The attacker calls `verify_transaction_inclusion_v2` with:
- `coinbase_tx_id = X`, `coinbase_merkle_proof = P` → check 1 passes: `compute_root(X, 0, P) == root` ✓
- `tx_id = X`, `tx_index = 0`, `merkle_proof = P` → check 2 passes: `compute_root(X, 0, P) == root` ✓
- Length check: `P.len() == P.len()` ✓

Both checks independently pass against the same `merkle_root` using the same data. The function returns `true`, certifying that X — an internal node, not a real transaction — is included in block B.

This is structurally identical to the external report: two separate checks each independently satisfy `msg.value >= value` against the same deposit, with no aggregate check that `msg.value >= value1 + value2`. Here, two separate proof checks each independently satisfy `compute_root(...) == merkle_root` against the same root, with no aggregate check that the two proofs are for genuinely distinct leaf-level positions.

### Impact Explanation

Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion_v2` to authorize an action (e.g., releasing bridged funds, minting tokens, confirming a cross-chain payment) can be deceived into accepting a forged transaction proof. The attacker needs only a confirmed mainchain block — no privileged access, no key compromise. The 64-byte attack mitigation that v2 was specifically designed to provide is rendered ineffective.

### Likelihood Explanation

The entry point is fully public and unprivileged. Any NEAR account can call `verify_transaction_inclusion_v2` with attacker-controlled `ProofArgsV2`. The required internal node X is trivially derivable from any real Bitcoin block with more than one transaction (the parent of coinbase and tx[1] is always available). No brute-force or cryptographic work is required.

### Recommendation

The coinbase proof must be anchored to the actual coinbase transaction, not an arbitrary caller-supplied hash. Two complementary fixes:

1. **Require `coinbase_tx_id` to be the real coinbase**: the caller must supply the raw coinbase transaction bytes; the contract hashes them and uses the result as `coinbase_tx_id`, preventing substitution of an internal node.
2. **Cross-validate the two proof paths**: at each level, verify that the sibling node in the coinbase proof and the sibling node in the tx proof are consistent with a single Merkle tree (i.e., they share the same parent at each level above the fork point). This ensures both proofs are anchored in the same tree structure and neither can be an internal node masquerading as a leaf.

### Proof of Concept

```
Given: confirmed mainchain block B with merkle_root R, ≥2 transactions.
Let coinbase_hash = hash(tx[0]), second_tx_hash = hash(tx[1]).
Let X = double_sha256(coinbase_hash || second_tx_hash)  // internal node, depth N-1
Let P = [right_sibling_at_depth_N-1, ..., right_sibling_at_depth_0]  // proof for X, length N-1

Call verify_transaction_inclusion_v2({
    tx_id:                  X,
    tx_block_blockhash:     hash(B),
    tx_index:               0,
    merkle_proof:           P,          // length N-1
    coinbase_tx_id:         X,          // same internal node
    coinbase_merkle_proof:  P,          // same proof
    confirmations:          1,
})

// coinbase check: compute_root(X, 0, P) == R  ✓
// length check:   P.len() == P.len()           ✓
// tx check (v1):  compute_root(X, 0, P) == R  ✓
// returns: true   — X (an internal node) is falsely certified as a transaction in B
```

### Citations

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
