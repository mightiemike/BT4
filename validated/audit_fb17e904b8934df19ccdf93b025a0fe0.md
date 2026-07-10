I have all the code needed to evaluate this claim. Let me trace the exact execution path.

**The call chain:**

`verify_transaction_inclusion_v2` (lines 347–369) performs two checks:
1. `merkle_proof.len() == coinbase_merkle_proof.len()` [1](#0-0) 
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == header.block_header.merkle_root` [2](#0-1) 

Then it calls `self.verify_transaction_inclusion(args.into())`. The `From<ProofArgsV2> for ProofArgs` conversion drops `coinbase_tx_id` and `coinbase_merkle_proof`, forwarding only `tx_id`, `tx_index`, `merkle_proof`, and `confirmations`. [3](#0-2) 

The inner `verify_transaction_inclusion` then independently re-fetches the header and checks `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root`. [4](#0-3) 

**The critical flaw:**

The coinbase check at line 358–365 does **not** verify that `coinbase_tx_id` is the actual coinbase transaction (the real first transaction in the block). It only verifies that *some* hash at index 0 with *some* proof reconstructs the merkle root. An attacker can supply an internal node as `coinbase_tx_id`.

**Concrete 4-tx attack:**

For a block with transactions `[A, B, C, D]` where `A` is the real coinbase:
- `AB = hash(A, B)`, `CD = hash(C, D)`, `root = hash(AB, CD)`

Attacker submits:
- `coinbase_tx_id = AB` (internal node)
- `coinbase_merkle_proof = [CD]` (length 1)
- `compute_root_from_merkle_proof(AB, 0, [CD])` = `hash(AB, CD)` = `root` ✓ — coinbase check passes
- `tx_id = AB` (same internal node)
- `tx_index = 0`
- `merkle_proof = [CD]` (length 1, matches coinbase proof length)
- Inner call: `compute_root_from_merkle_proof(AB, 0, [CD])` = `root` ✓ — returns `true`

Both length-equality and coinbase-proof guards pass. The inner call returns `true` for the internal node `AB`.

`compute_root_from_merkle_proof` itself has no guard against this — it simply walks the proof path regardless of whether the starting hash is a leaf or an internal node. [5](#0-4) 

---

### Title
Coinbase Mitigation Bypass via Internal-Node as Fake Coinbase in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion_v2` does not verify that `coinbase_tx_id` is the actual coinbase transaction. An attacker can supply an internal Merkle tree node as both `coinbase_tx_id` and `tx_id`, satisfying all guards and causing the function to return `true` for a non-existent transaction.

### Finding Description
The coinbase-proof guard at lines 358–365 checks only that `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`. It does not enforce that `coinbase_tx_id` equals the actual first transaction in the block. Because `compute_root_from_merkle_proof` is a pure arithmetic function with no leaf-vs-internal-node distinction, any hash that satisfies the equation at index 0 is accepted as the "coinbase." An internal node at depth `d` from the root satisfies this equation with a proof of length `d`. By setting `coinbase_tx_id = tx_id = <internal_node>` and `coinbase_merkle_proof = merkle_proof = <proof_of_length_d>`, the attacker satisfies both the length-equality check and the coinbase-proof check, then the forwarded inner call also passes.

### Impact Explanation
Any caller (no special role required — `verify_transaction_inclusion_v2` is a public `#[pause]`-gated view) can make the contract assert that an internal Merkle node is an included transaction. Downstream contracts or bridges that rely on this return value to authorize withdrawals, cross-chain transfers, or payment confirmations will accept fraudulent inclusion proofs.

### Likelihood Explanation
The attack requires only knowledge of a submitted block's Merkle tree structure (publicly available from Bitcoin RPC) and the ability to call the NEAR contract. No privileged role, leaked key, or social engineering is needed. The proof is constructible offline from any 4-tx (or larger even-tx-count) block.

### Recommendation
In `verify_transaction_inclusion_v2`, after the coinbase proof check, add an explicit assertion that `args.tx_id != args.coinbase_tx_id` when `args.tx_index == 0` is not the coinbase case, **and** independently verify the coinbase proof using a known-good coinbase hash obtained from a trusted source, or at minimum assert `args.coinbase_tx_id` is not equal to any intermediate node that could also satisfy the root equation. The most robust fix is to verify the coinbase proof against a separately stored or relayer-submitted coinbase txid, not one supplied by the caller.

### Proof of Concept
```
Block: 4 txs [A, B, C, D], A = coinbase
AB = hash(A,B), CD = hash(C,D), root = hash(AB,CD)

Call verify_transaction_inclusion_v2 with:
  tx_id                = AB          // internal node
  tx_block_blockhash   = <block>
  tx_index             = 0
  merkle_proof         = [CD]        // length 1
  coinbase_tx_id       = AB          // same internal node
  coinbase_merkle_proof= [CD]        // length 1 — lengths match ✓
  confirmations        = 0

Guard 1: len([CD]) == len([CD])  → pass ✓
Guard 2: compute_root(AB, 0, [CD]) = hash(AB,CD) = root  → pass ✓
Inner:   compute_root(AB, 0, [CD]) = hash(AB,CD) = root  → returns true ✓

Result: verify_transaction_inclusion_v2 returns true for internal node AB.
```

### Citations

**File:** contract/src/lib.rs (L318-322)
```rust
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
