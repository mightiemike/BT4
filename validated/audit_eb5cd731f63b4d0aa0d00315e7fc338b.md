### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Allowing Caller-Supplied Internal Merkle Node as `tx_id` to Forge Proof Results — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function is still a live, publicly callable NEAR entry point. It accepts a fully caller-controlled `tx_id` with no validation that the value corresponds to a real transaction hash. An unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id` together with a crafted proof path, causing the function to return `true` for a transaction that does not exist. Any recipient contract that gates fund release or state transitions on this result is permanently deceived — the false acceptance cannot be undone.

---

### Finding Description

`verify_transaction_inclusion` was deprecated in favour of `verify_transaction_inclusion_v2`, which adds a coinbase Merkle proof check specifically to block the 64-byte transaction forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions). However, Rust's `#[deprecated]` attribute is a compile-time lint only; it imposes no runtime restriction. The function remains a fully reachable public method on the deployed contract. [1](#0-0) 

The function's own documentation acknowledges the risk explicitly:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification." [2](#0-1) 

The entire proof check reduces to a single comparison with no `tx_id` validation: [3](#0-2) 

`compute_root_from_merkle_proof` in `merkle-tools` treats whatever hash is passed as the leaf unconditionally: [4](#0-3) 

`ProofArgs` exposes all five fields — `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations` — as fully caller-supplied with no server-side constraints: [5](#0-4) 

The v2 path that does enforce the coinbase guard is bypassed entirely when a caller invokes v1 directly: [6](#0-5) 

---

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion` (v1) and gates an irreversible action — releasing bridged funds, minting tokens, updating cross-chain state — on its `true` return value can be permanently deceived. The contract has no way to distinguish a genuine `true` from a forged one after the fact. The false acceptance is unrecoverable: once the downstream action executes, the funds or state change cannot be rolled back.

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte Merkle forgery technique is publicly documented and well understood. Every real Bitcoin block contains internal Merkle nodes whose hashes can be extracted from publicly available block data. The attacker only needs to pick a confirmed block already tracked by the light client, extract an internal node hash, and compute the corresponding sibling path — all from public information.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely, or add a runtime guard (e.g., `env::panic_str("use verify_transaction_inclusion_v2")`) at the top of the function body so that on-chain calls are rejected unconditionally. Deprecation comments alone do not prevent external callers from invoking the vulnerable path.

---

### Proof of Concept

1. Identify a Bitcoin block `B` already accepted by the light client (present in `mainchain_header_to_height`).
2. From public block data, extract any internal Merkle node `N` at tree depth `d`. `N` is the double-SHA256 of two concatenated child hashes `H1 ∥ H2`.
3. Treating `N` as a leaf at position `tx_index` (the position of the left child `H1` in the bottom layer), compute the sibling path from that position up to the root — this path is entirely derivable from the public block's transaction list.
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real txid)
   - `tx_block_blockhash = hash(B)`
   - `tx_index` = position computed in step 3
   - `merkle_proof` = sibling path from step 3
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(N, tx_index, merkle_proof)` reconstructs the real Merkle root of `B`, so the comparison at line 322 evaluates to `true`.
6. The function returns `true` for a transaction that does not exist in block `B`. Any recipient contract acting on this result is permanently deceived. [7](#0-6) [8](#0-7)

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
