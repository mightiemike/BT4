### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Security Check — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR function despite being deprecated. The security-critical coinbase Merkle proof check — introduced in `verify_transaction_inclusion_v2` specifically to block the 64-byte transaction Merkle proof forgery attack — is only enforced in v2. Any unprivileged caller can invoke v1 directly, bypassing that check entirely and obtaining a `true` result for a forged transaction inclusion proof.

---

### Finding Description

The contract exposes two SPV verification entry points:

**v2 (secure path):** `verify_transaction_inclusion_v2` first validates a coinbase Merkle proof against the block's `merkle_root`, then delegates to v1. [1](#0-0) 

This coinbase check is the sole mitigation for the well-known 64-byte transaction Merkle proof forgery vulnerability (referenced in the code's own doc comment at line 268). It ensures the Merkle tree has the correct leaf-level structure by anchoring the proof depth to the coinbase transaction at position 0.

**v1 (insecure path):** `verify_transaction_inclusion` is marked `#[deprecated]` but remains `pub` and carries only a `#[pause]` guard (which only blocks calls when the contract is administratively paused, not under normal operation). [2](#0-1) 

Rust's `#[deprecated]` attribute emits a compiler warning only — it imposes zero runtime restriction. The function is fully callable by any NEAR account at any time the contract is unpaused.

The structural parallel to the Argo bug is exact:

| Argo | BTC Light Client |
|---|---|
| `argo_liquidate::liquidate_withdraw` (wrapper with checks) | `verify_transaction_inclusion_v2` (wrapper with coinbase check) |
| `argo_engine::liquidate_repay` (no capability check) | `verify_transaction_inclusion` v1 (no coinbase check) |
| Attacker calls `liquidate_repay` directly, repays 1 token | Attacker calls v1 directly, submits forged `tx_id` |

---

### Impact Explanation

An attacker can supply an internal Merkle tree node hash as `tx_id` along with a valid Merkle path for that node. Without the coinbase anchor check, `compute_root_from_merkle_proof` will compute a root that matches the stored `header.block_header.merkle_root`, and the function returns `true` for a transaction that does not exist on-chain. [3](#0-2) 

Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) and acts on a `true` result — e.g., releasing funds, minting tokens, or updating state — can be deceived into accepting a fabricated Bitcoin transaction as confirmed.

---

### Likelihood Explanation

The entry point is fully open to any unprivileged NEAR account. No staking, role, or privileged key is required. The 64-byte transaction forgery technique is publicly documented (the code itself links to the BitMEX writeup at line 268). The only precondition is that the attacker knows a valid block hash already accepted by the contract, which is public on-chain state readable via `get_block_hash_by_height` or `get_last_block_header`. [4](#0-3) 

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with an explicit runtime panic (not just `#[deprecated]`). The preferred fix is to make v1 `pub(crate)` or private so it can only be reached through v2's coinbase-validated path, mirroring the Argo recommendation of flattening the architecture so the unchecked inner function is no longer directly reachable.

---

### Proof of Concept

1. Read any accepted mainchain block hash `B` via `get_block_hash_by_height(h)`.
2. Construct a crafted `tx_id` that is an internal Merkle node of block `B` (a 64-byte-transaction-style forgery as described at https://www.bitmex.com/blog/64-Byte-Transactions).
3. Compute a valid `merkle_proof` path from that internal node up to `B`'s `merkle_root`.
4. Call `verify_transaction_inclusion` directly (not v2) with `tx_id = <forged_node>`, `tx_block_blockhash = B`, `tx_index = <matching index>`, `merkle_proof = <crafted path>`, `confirmations = 1`.
5. The function returns `true` — no coinbase anchor check is performed, and `compute_root_from_merkle_proof` produces the correct root from the internal node.
6. Any recipient contract that trusted this result would accept the forged transaction as confirmed on Bitcoin. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L213-220)
```rust
    pub fn get_block_hash_by_height(&self, height: u64) -> Option<H256> {
        self.mainchain_height_to_header.get(&height)
    }

    #[allow(clippy::needless_pass_by_value)]
    pub fn get_height_by_block_hash(&self, blockhash: H256) -> Option<u64> {
        self.mainchain_header_to_height.get(&blockhash)
    }
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
