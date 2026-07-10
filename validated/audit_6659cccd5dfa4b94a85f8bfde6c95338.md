### Title
Merkle Proof Forgery via Internal-Node Hash Substitution Bypasses Coinbase Mitigation in `verify_transaction_inclusion_v2` — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte Merkle proof forgery attack by requiring a coinbase proof. However, neither `coinbase_tx_id` nor `tx_id` is validated to be a leaf-node hash (a real transaction hash). An unprivileged NEAR caller can substitute any publicly-known internal Merkle tree node hash for both fields, satisfy every check in the function, and receive a `true` return value for a transaction that was never included in any Bitcoin block.

---

### Finding Description

`verify_transaction_inclusion_v2` performs three checks before returning:

1. **Length parity**: `merkle_proof.len() == coinbase_merkle_proof.len()`
2. **Coinbase check**: `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == header.block_header.merkle_root`
3. **Transaction check** (delegated to the deprecated `verify_transaction_inclusion`): `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.block_header.merkle_root` [1](#0-0) 

The coinbase check at step 2 is the intended mitigation: it is supposed to force the prover to know the real coinbase transaction hash, thereby constraining the tree structure. However, `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` treats its first argument as an opaque 32-byte value and applies the standard left/right hashing rule based on position parity — it never validates that the value is a leaf node rather than an internal node. [2](#0-1) 

For any Bitcoin block whose Merkle tree is publicly known (all of them), consider a 4-transaction block:

```
        root = hash(A, B)
       /                \
  A = hash(tx0,tx1)    B = hash(tx2,tx3)
```

An attacker sets:
- `coinbase_tx_id = A` (an internal node hash, not the real coinbase)
- `coinbase_merkle_proof = [B]`

`compute_root_from_merkle_proof(A, 0, [B])`:
- position 0 is even → `hash(A, B) = root` ✓

The coinbase check passes. The attacker then sets:
- `tx_id = A`, `tx_index = 0`, `merkle_proof = [B]`

Both proofs have length 1 (parity check passes). The transaction check also computes `hash(A, B) = root` ✓. The function returns `true` for "transaction" `A`, which is an internal node and was never a real transaction.

The deprecated `verify_transaction_inclusion` (still publicly callable) has the same root cause with no coinbase guard at all — it only requires `merkle_proof` to be non-empty. [3](#0-2) 

The root cause in both cases is identical to H-2: **no validation that the supplied identifier (`tx_id`, `coinbase_tx_id`) is a legitimate leaf-node value rather than a crafted or repurposed internal value**. Just as `OracleLess.createOrder()` accepted any address as `tokenIn`, these functions accept any 32-byte hash as a transaction identifier.

---

### Impact Explanation

Any downstream contract or bridge that calls `verify_transaction_inclusion_v2` to gate fund releases (e.g., "release NEAR-side tokens only if this Bitcoin transaction is confirmed") can be drained. The attacker supplies a forged proof for a non-existent transaction, the function returns `true`, and the bridge releases funds. The corrupted protocol value is the boolean proof result returned by the verification API — the exact invariant the light client is designed to protect.

---

### Likelihood Explanation

The attack requires:
- No special NEAR role or privilege
- No Bitcoin mining or on-chain Bitcoin activity
- No computational work beyond looking up a block's public Merkle tree

Every Bitcoin block's Merkle tree is fully public. The attacker only needs to pick any confirmed block, read its internal node hashes from a block explorer, and call the NEAR function with those values. Likelihood is high for any deployment where a bridge or settlement contract consumes the verification result.

---

### Recommendation

1. **Validate coinbase transaction size**: Require the caller to supply the raw coinbase transaction bytes (not just the hash), verify `sha256d(raw_coinbase) == coinbase_tx_id`, and reject any raw coinbase shorter than 65 bytes (internal nodes are exactly 64 bytes, so a valid transaction is always distinguishable).
2. **Enforce proof-depth consistency**: Require that `merkle_proof.len()` equals `ceil(log2(tx_count))` for the block, and supply `tx_count` as a verified field. This prevents using a shallow proof that skips leaf-level validation.
3. **Remove or hard-disable `verify_transaction_inclusion`**: The deprecated function is still callable and has no coinbase guard. It should be gated behind a role or removed entirely, not merely annotated `#[deprecated]`.

---

### Proof of Concept

Given a real Bitcoin block (e.g., height 685452, `merkle_root = 6dc8856d...`) with a known 4-transaction Merkle tree where `A = hash(tx0, tx1)` and `B = hash(tx2, tx3)`:

```
// Attacker calls on NEAR:
verify_transaction_inclusion_v2({
    tx_id:                  A,          // internal node, not a real tx
    tx_block_blockhash:     <valid mainchain block hash>,
    tx_index:               0,
    merkle_proof:           [B],        // length 1
    coinbase_tx_id:         A,          // same internal node
    coinbase_merkle_proof:  [B],        // length 1 — parity check passes
    confirmations:          1,
})
// Returns: true
// Actual state: no transaction with hash A exists in that block
``` [2](#0-1) [1](#0-0) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
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

**File:** btc-types/src/contract_args.rs (L26-47)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}

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
