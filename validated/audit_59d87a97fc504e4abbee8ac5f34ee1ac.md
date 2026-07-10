### Title
Zero `confirmations` Parameter Bypasses Depth Guard, Enabling Zero-Confirmation SPV Proof Acceptance — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2` which delegates to it) accepts `confirmations = 0` without rejection. When `confirmations` is zero, the depth guard `depth + 1 >= args.confirmations` reduces to `u64 >= 0`, which is unconditionally true. Any block on the mainchain — including the chain tip with zero depth — passes the check. An unprivileged NEAR caller can supply `confirmations = 0` alongside a valid Merkle proof for a freshly submitted block and receive a `true` verification result, giving consuming contracts a false guarantee of finality.

### Finding Description

`verify_transaction_inclusion` enforces a minimum confirmation depth with:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

When `args.confirmations == 0`, the expression evaluates to `depth + 1 >= 0`. Because `depth + 1` is a `u64`, this is always `true` regardless of how shallow the block is. The guard is completely bypassed.

`verify_transaction_inclusion_v2` passes `confirmations` through unchanged via `args.into()`:

```rust
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

The `ProofArgs` and `ProofArgsV2` structs accept `confirmations: u64` with no lower-bound constraint: [3](#0-2) 

There is no `require!(args.confirmations > 0, ...)` guard anywhere in either function.

### Impact Explanation

A consuming contract (e.g., a NEAR-side bridge that releases funds upon Bitcoin payment proof) calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2`. An attacker:

1. Broadcasts a Bitcoin transaction.
2. Submits the containing block header to the NEAR contract via `submit_blocks` (open to any caller with sufficient deposit).
3. Immediately calls `verify_transaction_inclusion` with `confirmations = 0` and a valid Merkle proof.
4. The function returns `true`.
5. The consuming contract releases funds.
6. The attacker double-spends on the Bitcoin side by triggering a reorg that removes the block.

The consuming contract accepted a proof with zero depth, providing no reorg protection. The corrupted value is the boolean verification result returned to the caller — it is `true` when it should be `false` (or the call should panic).

### Likelihood Explanation

The entry path is fully unprivileged: any NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with attacker-chosen arguments. The `confirmations` field is a plain `u64` in a Borsh-serialized struct with no on-chain minimum enforcement. The attack requires only that a consuming contract exists that trusts the return value — the primary purpose of this contract is to serve exactly such consumers.

### Recommendation

Add an explicit guard at the top of `verify_transaction_inclusion` (and/or in `verify_transaction_inclusion_v2` before delegating):

```rust
require!(args.confirmations >= 1, "Confirmations must be at least 1");
```

Alternatively, change the depth comparison from `>=` to `>` so that `confirmations = 0` is treated as requiring at least one confirmation:

```rust
// current (broken when confirmations == 0):
depth + 1 >= args.confirmations

// fixed:
depth + 1 > args.confirmations   // 0 confirmations → 1 > 0 still passes, but semantics shift
```

The cleaner fix is the explicit `require!` guard, mirroring the fix recommended in H-06: reject the degenerate zero input rather than relying on the arithmetic to behave correctly.

### Proof of Concept

Assume the contract is initialized with `gc_threshold = 1000` and one block has been submitted at height `H` (the tip).

```
confirmations = 0
tx_block_blockhash = <hash of tip block at height H>
heaviest_block_header.block_height = H
target_block_height = H

depth = H.saturating_sub(H) = 0
check: 0 + 1 >= 0  →  1 >= 0  →  true  ✓ (guard bypassed)
```

The Merkle proof check then runs normally. If the attacker supplies a valid Merkle proof for a real transaction in that block, `verify_transaction_inclusion` returns `true` — for a transaction with zero confirmation depth. [4](#0-3) [5](#0-4)

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

**File:** btc-types/src/contract_args.rs (L16-36)
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
```
