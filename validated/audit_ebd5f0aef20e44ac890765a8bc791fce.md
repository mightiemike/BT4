Looking at the `compute_root_from_merkle_proof` function and `verify_transaction_inclusion` carefully to trace the exact arithmetic.

I have all the information needed. Let me trace the exact arithmetic to confirm or deny the vulnerability.

**Concrete arithmetic trace — 3-tx block [T0, T1, T2]:**

Bitcoin Merkle tree with odd-width duplication:
- Level 0 (padded): `[T0, T1, T2, T2]`
- Level 1: `[H(T0,T1), H(T2,T2)]`
- Root: `H(H(T0,T1), H(T2,T2))`

**Proof for T2 at real position 2, proof = `[T2, H(T0,T1)]`:**

In `compute_root_from_merkle_proof` ( [1](#0-0) ):
- iter 1: `current_position=2`, `2%2==0` → `compute_hash(T2, T2)` = `H(T2,T2)`, position → 1
- iter 2: `current_position=1`, `1%2==1` → `compute_hash(H(T0,T1), H(T2,T2))` = root ✓

**Same proof for phantom position 3, proof = `[T2, H(T0,T1)]`:**
- iter 1: `current_position=3`, `3%2==1` → `compute_hash(T2, T2)` = `H(T2,T2)`, position → 1
- iter 2: `current_position=1`, `1%2==1` → `compute_hash(H(T0,T1), H(T2,T2))` = root ✓

**Both produce the identical root.** The proof `(tx_id=T2, proof=[T2, H(T0,T1)])` verifies for both `tx_index=2` and `tx_index=3`. Position 3 does not exist in the block.

**Why the light client cannot reject this:** The contract stores only the Merkle root per block header — it never stores the transaction count. [2](#0-1)  There is no upper-bound check on `tx_index` anywhere in `verify_transaction_inclusion` or in `compute_root_from_merkle_proof`. [3](#0-2) 

**Impact path:** A downstream bridge contract that uses `(tx_id, tx_index)` as its replay-protection key (a common pattern) would mark `(T2, 2)` as spent after the first deposit, but `(T2, 3)` would pass `verify_transaction_inclusion` and appear unspent, enabling a second mint or unlock for the same on-chain deposit.

**Does `verify_transaction_inclusion_v2` fix this?** No. It adds a coinbase proof check, then calls `verify_transaction_inclusion` internally. [4](#0-3)  The phantom-position arithmetic is identical in both paths.

---

### Title
Duplicate-last-leaf phantom position allows `verify_transaction_inclusion` to return `true` for a non-existent `tx_index` — (`merkle-tools/src/lib.rs`)

### Summary
`compute_root_from_merkle_proof` performs no upper-bound check on `transaction_position`. In any odd-width Bitcoin Merkle tree the last leaf is duplicated during tree construction. This makes the proof for the real last leaf (position N−1) arithmetically identical to a proof for the phantom position N. `verify_transaction_inclusion` therefore returns `true` for `tx_index = N`, a position that corresponds to no real transaction in the block.

### Finding Description
`compute_root_from_merkle_proof` iterates over the proof array, branching left or right based on `current_position % 2`, then halves the position. [1](#0-0)  No check is made that `transaction_position` is less than the actual leaf count. The contract stores only the Merkle root per block; the transaction count is never recorded. [2](#0-1) 

For a block with an odd number of transactions N, Bitcoin's Merkle construction pads the leaf array by duplicating the last leaf, making the tree width N+1. The proof path for position N−1 (even) and position N (odd) both reduce to `compute_hash(last_leaf, last_leaf)` at the first step, then follow the identical upper path. The resulting computed root equals the stored Merkle root in both cases.

`verify_transaction_inclusion` passes the caller-supplied `tx_index` directly to `compute_root_from_merkle_proof` with no bounds validation. [5](#0-4)  `verify_transaction_inclusion_v2` inherits the same flaw because it delegates to `verify_transaction_inclusion` after the coinbase check. [6](#0-5) 

### Impact Explanation
A downstream bridge contract that uses `(tx_id, tx_index)` as its deposit-replay key will treat `(T_last, N−1)` and `(T_last, N)` as two distinct deposit events. After the real deposit at index N−1 is processed and marked spent, the attacker submits the identical proof with `tx_index = N`. The light client returns `true`; the bridge mints or unlocks a second time for the same on-chain transaction. This constitutes direct theft of bridged funds.

### Likelihood Explanation
The function is public and callable by any account. The attacker needs only: a confirmed block with an odd transaction count (the majority of Bitcoin blocks), the real Merkle proof for the last transaction (publicly available from any Bitcoin node), and knowledge of the bridge's replay-key scheme. No privileged role, leaked key, or social engineering is required.

### Recommendation
1. **Short-term:** Require callers to supply the total transaction count for the block and reject any `tx_index >= tx_count`. Store or commit to the transaction count in the block header metadata, or require it as part of the proof arguments.
2. **Long-term:** Adopt a proof format that encodes the leaf count (e.g., the `n_txs` field from the Bitcoin block header coinbase path), so the verifier can enforce `tx_index < n_txs` without trusting the caller.
3. Apply the same fix to `verify_transaction_inclusion_v2`, which inherits the flaw.

### Proof of Concept
```
Block: 3 transactions [T0, T1, T2]
Merkle tree (padded): [T0, T1, T2, T2]
Level 1: [H(T0,T1), H(T2,T2)]
Root R = H(H(T0,T1), H(T2,T2))

Proof P = [T2, H(T0,T1)]

Call 1 (real deposit, already processed):
  verify_transaction_inclusion(tx_id=T2, tx_index=2, merkle_proof=P, ...)
  → compute_root_from_merkle_proof:
      pos=2 (even): hash(T2, T2) = H(T2,T2), pos→1
      pos=1 (odd):  hash(H(T0,T1), H(T2,T2)) = R
  → R == stored_root → returns true ✓

Call 2 (phantom position, attacker):
  verify_transaction_inclusion(tx_id=T2, tx_index=3, merkle_proof=P, ...)
  → compute_root_from_merkle_proof:
      pos=3 (odd):  hash(T2, T2) = H(T2,T2), pos→1
      pos=1 (odd):  hash(H(T0,T1), H(T2,T2)) = R
  → R == stored_root → returns true ✓

Bridge replay key (T2, 2) is spent; (T2, 3) is not → second mint executes.
```

### Citations

**File:** merkle-tools/src/lib.rs (L42-49)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }
```

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
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
