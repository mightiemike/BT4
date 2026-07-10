### Title
Unvalidated `tx_id` in `verify_transaction_inclusion` Enables 64-Byte Merkle Proof Forgery, Allowing Proof of Non-Existent Transactions - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) accepts any 32-byte value as `tx_id` without validating that it is a leaf-level transaction hash rather than an internal Merkle tree node. Because `compute_root_from_merkle_proof` is purely positional, an attacker can supply an internal Merkle node as `tx_id` with a shortened proof path and cause the function to return `true` for a transaction that does not exist. Any downstream NEAR contract that calls this function as an SPV oracle to authorize fund releases or cross-chain state transitions is directly exploitable.

---

### Finding Description

`verify_transaction_inclusion` at `contract/src/lib.rs:288` performs exactly one cryptographic check:

```
compute_root_from_merkle_proof(args.tx_id, args.tx_index, &args.merkle_proof)
    == header.block_header.merkle_root
``` [1](#0-0) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` iterates over the proof hashes and repeatedly calls `compute_hash`, which is `double_sha256(left || right)`. It treats `transaction_hash` as an opaque 32-byte value and applies the same hashing logic regardless of whether it is a leaf or an internal node. [2](#0-1) 

**The 64-byte forgery attack** exploits the fact that Bitcoin's Merkle tree uses the same hash function at every level. Given a real block with transactions `[T0, T1, T2, T3]`:

```
Level 1 (leaves): T0, T1, T2, T3
Level 2 (internal): N01 = hash(T0 || T1),  N23 = hash(T2 || T3)
Level 3 (root):    Root = hash(N01 || N23)
```

An attacker submits:
- `tx_id = N01` (an internal node, not a real transaction)
- `tx_index = 0`
- `merkle_proof = [N23]`

`compute_root_from_merkle_proof(N01, 0, [N23])` computes `hash(N01 || N23) = Root`, which matches `header.block_header.merkle_root`. The function returns `true` for a transaction that was never broadcast or mined.

The function's own deprecation notice acknowledges this exact class of attack: [3](#0-2) 

`verify_transaction_inclusion_v2` closes the gap by first verifying a coinbase proof at index 0, which forces the proof depth to match the real tree depth and prevents an attacker from "starting" the proof at an internal level: [4](#0-3) 

However, v1 remains a live, callable public method on the contract. There is no on-chain enforcement preventing callers from invoking it. [5](#0-4) 

---

### Impact Explanation

Any downstream NEAR smart contract that calls `verify_transaction_inclusion` (v1) as its SPV oracle — for example, to release bridged funds upon proof of a Bitcoin deposit — can be tricked into authorizing a payout for a transaction that never occurred. The attacker needs only a real confirmed block hash (publicly available) and knowledge of its Merkle tree structure (also public). The forged proof passes all on-chain checks and the function returns `true`, giving the downstream contract a false positive it cannot distinguish from a legitimate proof. This maps directly to the external report's impact: an unauthorized party causes fund movement without the consent of the rightful owner, using a missing cryptographic validation step as the root cause. [6](#0-5) 

---

### Likelihood Explanation

The attack requires no privileged access. Any unprivileged NEAR account can call `verify_transaction_inclusion` directly. The required inputs — a confirmed block hash and its Merkle tree — are entirely public. The forgery computation is trivial: pick any internal node from the public Merkle tree, shorten the proof by one level, and submit. The only precondition is that at least one downstream consumer of v1 exists, which is realistic given the function has been present since before v0.5.0 and integrators may not have migrated. [7](#0-6) 

---

### Recommendation

Remove or gate `verify_transaction_inclusion` (v1) so it is no longer callable by external accounts. Options in order of preference:

1. **Remove the method entirely** from the public ABI. Callers must migrate to `verify_transaction_inclusion_v2`.
2. **Internally redirect** v1 to v2 by requiring a coinbase proof argument, making the old signature a breaking change that forces callers to update.
3. At minimum, add a `require!(false, "deprecated: use verify_transaction_inclusion_v2")` guard so any call panics immediately, preventing silent misuse.

The coinbase-proof technique in v2 is the correct fix: by anchoring the proof at index 0 (the coinbase transaction, which is always a real leaf), the proof depth is pinned to the actual tree depth, making it impossible to substitute an internal node as `tx_id`. [8](#0-7) 

---

### Proof of Concept

**Setup**: A real Bitcoin mainnet block is already stored in the contract (e.g., height 685452, submitted via `submit_blocks`). Its Merkle root and transaction list are public.

**Step 1 – Compute an internal node offline**:
```
T0 = coinbase_txid  (32 bytes, known from block explorer)
T1 = second_txid    (32 bytes, known from block explorer)
N01 = double_sha256(T0 || T1)   # internal node at level 2
```

**Step 2 – Compute the sibling of N01**:
```
T2, T3 = third and fourth txids
N23 = double_sha256(T2 || T3)   # sibling internal node
```

**Step 3 – Verify offline that the forgery works**:
```
double_sha256(N01 || N23) == block.merkle_root   # must be true
```

**Step 4 – Call the contract**:
```rust
verify_transaction_inclusion(ProofArgs {
    tx_id:             N01,              // internal node, not a real tx
    tx_block_blockhash: block_hash,      // real confirmed block
    tx_index:          0,               // position of N01 in the "virtual" level-2 tree
    merkle_proof:      vec![N23],       // one-element proof
    confirmations:     1,
})
```

**Expected result**: The function returns `true`. `compute_root_from_merkle_proof(N01, 0, [N23])` = `double_sha256(N01 || N23)` = `block.merkle_root`. The contract reports that the non-existent "transaction" `N01` is confirmed in the block.

**Downstream impact**: A bridge contract that calls `verify_transaction_inclusion` and releases funds on a `true` result will release funds to the attacker for a deposit that never happened. [9](#0-8) [2](#0-1)

### Citations

**File:** contract/src/lib.rs (L265-279)
```rust
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** merkle-tools/src/lib.rs (L54-60)
```rust
fn compute_hash(first_tx_hash: &H256, second_tx_hash: &H256) -> H256 {
    let mut concat_inputs = Vec::with_capacity(64);
    concat_inputs.extend(first_tx_hash.0);
    concat_inputs.extend(second_tx_hash.0);

    double_sha256(&concat_inputs)
}
```

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
