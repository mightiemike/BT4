### Title
Broken Coinbase Mitigation Allows Internal Merkle Node to Pass as Valid Transaction — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` accepts a caller-supplied `coinbase_tx_id` without verifying it against any trusted source. An attacker can supply an internal Merkle tree node as both `coinbase_tx_id` and `tx_id`, with a truncated proof of matching depth, causing both the coinbase check and the transaction inclusion check to pass. The function returns `true` for a hash that is not a real transaction.

---

### Finding Description

The coinbase mitigation introduced in v2 is intended to prevent the 64-byte transaction Merkle proof forgery attack (CVE-2012-2459). The design intent is: by requiring a valid proof for the coinbase (always at index 0, always a real leaf), and requiring that proof to have the same depth as the tx proof, the verifier ensures the claimed tx is also at leaf depth.

The flaw is that `coinbase_tx_id` is entirely caller-controlled and is never checked against any trusted value. The coinbase check only verifies:

```
compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == header.merkle_root
``` [1](#0-0) 

An internal Merkle node satisfies this equation with a shorter proof. Consider a 4-transaction block `[T0, T1, T2, T3]`:

```
N01  = hash(T0, T1)
N23  = hash(T2, T3)
Root = hash(N01, N23)
```

The attacker submits:
- `coinbase_tx_id = N01`, `coinbase_merkle_proof = [N23]`
- `tx_id = N01`, `tx_index = 0`, `merkle_proof = [N23]`

**Coinbase check:**
`compute_root_from_merkle_proof(N01, 0, [N23])` → `hash(N01, N23)` = `Root` ✓ [2](#0-1) 

**TX inclusion check** (inside `verify_transaction_inclusion`):
`compute_root_from_merkle_proof(N01, 0, [N23])` → `Root` ✓ [3](#0-2) 

Both checks pass. The function returns `true` for `tx_id = N01`, which is not a real transaction.

The length equality guard (`merkle_proof.len() == coinbase_merkle_proof.len()`) is satisfied trivially since the attacker uses the same proof for both. [4](#0-3) 

The non-emptiness guard on `merkle_proof` is also satisfied since the proof has one element. [5](#0-4) 

---

### Impact Explanation

Any caller can convince the contract that an arbitrary internal Merkle node is a valid transaction included in a canonical block. Downstream contracts or protocols that rely on `verify_transaction_inclusion_v2` returning `true` only for real transactions will accept forged inclusion claims. This directly breaks the security guarantee the v2 function was designed to provide.

---

### Likelihood Explanation

The attack requires only knowledge of the Merkle tree structure of any canonical block (publicly available from any Litecoin node). No privileged role, key, or social engineering is needed. The call is a public `view`-style NEAR contract call accessible to any unprivileged account.

---

### Recommendation

The `coinbase_tx_id` must be verified against a trusted source rather than accepted from the caller. Options:
1. Store the coinbase transaction hash in the block header record at submission time and compare against it inside `verify_transaction_inclusion_v2`.
2. Require the caller to supply the full raw coinbase transaction, compute its txid on-chain, and use that computed value for the coinbase check.

Simply requiring equal proof lengths and a self-consistent coinbase proof is insufficient when the coinbase identity itself is caller-supplied.

---

### Proof of Concept

Given a canonical block with 4 transactions `[T0, T1, T2, T3]` and known `N01 = hash(T0, T1)`, `N23 = hash(T2, T3)`, `Root = hash(N01, N23)`:

```rust
let args = ProofArgsV2 {
    tx_id:                  N01,   // internal node, not a real tx
    tx_block_blockhash:     block_hash,
    tx_index:               0,
    merkle_proof:           vec![N23],
    coinbase_tx_id:         N01,   // same internal node as fake coinbase
    coinbase_merkle_proof:  vec![N23],
    confirmations:          0,
};
// verify_transaction_inclusion_v2(args) returns true
```

Both `compute_root_from_merkle_proof(N01, 0, [N23])` calls return `Root`, matching `header.block_header.merkle_root`. The function returns `true` for a non-existent transaction.

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
```

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
```

**File:** merkle-tools/src/lib.rs (L38-51)
```rust
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
```
