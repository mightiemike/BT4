### Title
Unvalidated `tx_index` Bounds in `compute_root_from_merkle_proof` Allows Proof Reuse Across Multiple Positions - (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` accepts a `transaction_position` (sourced directly from the caller-supplied `tx_index`) without validating it is within the range `[0, 2^(merkle_proof.len()) - 1]`. Because the function only consumes the lower `n` bits of `transaction_position` (where `n = merkle_proof.len()`), a valid proof for position `X` also verifies for positions `X + k * 2^n` for any integer `k > 0`. This is the direct analog of the MIPS `readMem`/`writeMem` accepting a `uint32 _addr` without bounding it to the valid address space.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` navigates the Merkle tree by repeatedly testing `current_position % 2` and then halving `current_position /= 2` for each proof element: [1](#0-0) 

After iterating through all `n` proof elements, `current_position` equals `transaction_position / 2^n`. The function never asserts this remainder is zero, and it never checks that `transaction_position < 2^n`. Because only the lower `n` bits of `transaction_position` influence the left/right navigation decisions, positions `X` and `X + k * 2^n` produce **identical traversal paths** and therefore **identical computed roots**.

This function is called from both public contract entry points with the raw caller-supplied `tx_index`:

- `verify_transaction_inclusion` passes `usize::try_from(args.tx_index).unwrap()` directly, with no upper-bound check beyond `!args.merkle_proof.is_empty()`. [2](#0-1) 

- `verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after verifying the coinbase proof, inheriting the same unchecked `tx_index`. [3](#0-2) 

`tx_index` is typed as `u64` in `ProofArgs` and `ProofArgsV2`, so an attacker can supply any value from `0` to `u64::MAX`: [4](#0-3) 

---

### Impact Explanation

The broken invariant is: **a single Merkle proof should attest to exactly one leaf position**. Without the bounds check, one proof attests to infinitely many positions (`X`, `X + 2^n`, `X + 2*2^n`, …).

Concrete consequences for consumer contracts that call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2`:

1. **Coinbase-identity bypass**: A consumer that checks `tx_index == 0` to confirm a coinbase transaction can be deceived. An attacker holds a valid proof for the coinbase (position 0) and submits `tx_index = 2^n`. The proof still returns `true`, but the consumer's `tx_index == 0` guard fails, making the coinbase appear to be a non-coinbase transaction.

2. **Position-gated logic bypass**: Any consumer that gates logic on `tx_index` being within a specific range (e.g., "only process transactions at even indices", "reject index 0") can be bypassed by shifting the claimed index by a multiple of `2^n`.

3. **Out-of-range position acceptance**: The contract accepts and returns `true` for `tx_index` values that cannot correspond to any real transaction in the block (e.g., `tx_index = 2^63`), silently corrupting the semantic meaning of the verification result for any downstream consumer.

---

### Likelihood Explanation

The entry path is fully unprivileged: any NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with an arbitrary `tx_index`. No staking, role, or privileged access is required. The attacker only needs a legitimately valid Merkle proof for any real transaction in a confirmed block, which is publicly available from any Bitcoin node. The manipulation is a single field change (`tx_index = real_index + k * 2^proof_depth`) with no additional computational cost.

---

### Recommendation

Add a bounds check in `compute_root_from_merkle_proof` asserting that `transaction_position < 2^(merkle_proof.len())`:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    // Validate: position must be within the leaf range for this proof depth
    let max_valid_position = (1usize << merkle_proof.len()).saturating_sub(1);
    assert!(
        transaction_position <= max_valid_position,
        "tx_index out of bounds for the given proof depth"
    );

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

Additionally, after the loop, assert `current_position == 0` as a defense-in-depth invariant confirming the position was fully consumed.

---

### Proof of Concept

Given a block with 8 transactions (proof depth `n = 3`), the coinbase is at index 0. A valid proof `P` for index 0 satisfies `compute_root_from_merkle_proof(coinbase_hash, 0, P) == merkle_root`.

An attacker submits:
- `tx_id = coinbase_hash`
- `tx_index = 8` (= `0 + 1 * 2^3`, out of range)
- `merkle_proof = P` (the valid coinbase proof)

Navigation at each step:
- Step 0: `8 % 2 = 0` → left (same as index 0)
- Step 1: `4 % 2 = 0` → left (same as index 0)
- Step 2: `2 % 2 = 0` → left (same as index 0)

`compute_root_from_merkle_proof(coinbase_hash, 8, P)` returns the same root as for index 0. `verify_transaction_inclusion` returns `true`. A consumer contract checking `tx_index == 0` to identify the coinbase receives `tx_index = 8` and incorrectly concludes the transaction is not a coinbase, while the light client has confirmed its inclusion. [5](#0-4) [6](#0-5)

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

**File:** contract/src/lib.rs (L315-322)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L347-368)
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
```

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
