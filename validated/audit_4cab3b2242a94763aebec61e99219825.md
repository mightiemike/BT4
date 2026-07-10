### Title
`verify_transaction_inclusion_v2` Coinbase Proof Bypass Allows Internal Merkle Node to Be Proven as a Valid Transaction — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is designed to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase proof of the same depth as the transaction proof. However, the function accepts an attacker-controlled `coinbase_tx_id` without verifying it is the actual coinbase transaction. An unprivileged NEAR caller can supply a fake coinbase proof using an internal Merkle node, bypassing the mitigation entirely and causing the function to return `true` for an internal node that is not a real transaction.

---

### Finding Description

`verify_transaction_inclusion_v2` performs two checks before delegating to the deprecated `verify_transaction_inclusion`:

1. **Length parity check**: `args.merkle_proof.len() == args.coinbase_merkle_proof.len()`
2. **Coinbase proof check**: `compute_root_from_merkle_proof(args.coinbase_tx_id, 0, &args.coinbase_merkle_proof) == header.block_header.merkle_root` [1](#0-0) 

The intent of the coinbase check is to anchor the proof depth to a known real leaf (the coinbase transaction is always at position 0). If the coinbase proof has depth D, the tree has D levels, and requiring the tx proof to also have depth D is supposed to guarantee the tx is at the leaf level.

The broken invariant: the contract never verifies that `args.coinbase_tx_id` is the actual coinbase transaction of the block. It only verifies that whatever hash is supplied at position 0 with the given proof produces the correct Merkle root. Any internal node at depth D that happens to be the leftmost node at that depth satisfies this check, because the left-sibling path from any internal node to the root is a valid proof for position 0 at depth D.

The `compute_root_from_merkle_proof` function used for both checks is purely positional — it does not distinguish leaves from internal nodes: [2](#0-1) 

After the coinbase check passes, `verify_transaction_inclusion` is called. It re-reads the same header and checks the tx proof against `header.block_header.merkle_root`: [3](#0-2) 

Because both the fake coinbase proof and the internal-node tx proof are mathematically valid against the same Merkle root, both checks pass and the function returns `true`.

---

### Impact Explanation

Any downstream consumer (bridge, atomic swap, cross-chain lending protocol) that calls `verify_transaction_inclusion_v2` to confirm a Bitcoin transaction occurred can be deceived. An attacker can prove that an internal Merkle node — which is not a real transaction — is "included" in a confirmed block. If the consumer allows the caller to specify the `tx_id` (e.g., "prove this tx_id is in this block"), the attacker can substitute any internal node and receive a `true` result, potentially triggering fund releases or state changes that should never occur.

The deprecated `verify_transaction_inclusion` explicitly documents this weakness: [4](#0-3) 

`verify_transaction_inclusion_v2` was introduced specifically to close this gap, but the gap remains because the coinbase identity is never verified.

---

### Likelihood Explanation

The attack requires only publicly available on-chain data. For any confirmed Bitcoin block, all transactions are public, so all internal Merkle nodes are computable. No privileged access, private keys, or social engineering is needed. The attacker calls `verify_transaction_inclusion_v2` directly as an unprivileged NEAR account. The function is public and only gated by the `#[pause]` macro, which is inactive under normal operation. [5](#0-4) 

---

### Recommendation

The contract must verify that `args.coinbase_tx_id` is the actual coinbase transaction of the block. Since the contract stores only the Merkle root (not individual transaction IDs), the caller must supply the raw coinbase transaction bytes. The contract should:

1. Accept the raw coinbase transaction bytes as an additional argument.
2. Compute `double_sha256(coinbase_tx_bytes)` on-chain and assert it equals `args.coinbase_tx_id`.
3. Verify the coinbase transaction is exactly 64 bytes or more (to rule out the 64-byte forgery class), or parse it to confirm it is a structurally valid Bitcoin transaction.

Alternatively, require the caller to prove the coinbase at its true leaf position using the full-depth proof (same depth as the tree), making it impossible to substitute an internal node.

---

### Proof of Concept

Consider a confirmed block whose Merkle tree has 4 transactions `[T0, T1, T2, T3]` where `T0` is the coinbase:

```
Level 0 (leaves):   T0,  T1,  T2,  T3
Level 1 (internal): H01=hash(T0,T1),  H23=hash(T2,T3)
Level 2 (root):     root=hash(H01,H23)
```

The attacker calls `verify_transaction_inclusion_v2` with:

```
tx_id                 = H23          // internal node, not a real transaction
tx_index              = 1
merkle_proof          = [H01]        // length 1
coinbase_tx_id        = H01          // fake "coinbase" — actually the left internal node
coinbase_merkle_proof = [H23]        // length 1
tx_block_blockhash    = <real confirmed block hash>
confirmations         = 1
```

Step-by-step execution:

| Check | Computation | Result |
|---|---|---|
| Length parity | `1 == 1` | ✓ |
| Coinbase proof | `compute_root(H01, 0, [H23])` = `hash(H01,H23)` = `root` | ✓ |
| Tx proof (inside v1) | `compute_root(H23, 1, [H01])` = `hash(H01,H23)` = `root` | ✓ |

The function returns `true` for `H23`, an internal node that is not a Bitcoin transaction. [6](#0-5) [2](#0-1)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
