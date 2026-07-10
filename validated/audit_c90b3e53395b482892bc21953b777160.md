### Title
Missing Lower-Bound Validation on `confirmations` Parameter Allows Zero-Confirmation Transaction Acceptance — (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations` value of `0` without any lower-bound check. With `confirmations = 0`, the depth guard always evaluates to true, so any transaction in any block — including the block at the current chain tip — is treated as fully confirmed. Any NEAR contract consuming this result can be tricked into releasing funds or executing actions against an unconfirmed Bitcoin transaction.

### Finding Description

In `verify_transaction_inclusion`, the only guard on `confirmations` is an upper-bound check:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

There is no lower-bound check (`require!(args.confirmations >= 1, ...)`). The subsequent depth guard is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `args.confirmations = 0`, the right-hand side is `0`, and the left-hand side is a `u64` that is always `>= 0`. The check unconditionally passes for every block in the chain, including the tip block (0 confirmations deep). The function then proceeds to verify the Merkle proof and returns `true` if it matches — confirming a transaction that has received zero confirmations.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase-proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `confirmations` field is defined as a plain `u64` with no constraints in `ProofArgs` and `ProofArgsV2`:

```rust
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
``` [4](#0-3) 

### Impact Explanation

**Impact: High.** A bridge, DEX, or any NEAR contract that calls `verify_transaction_inclusion{_v2}` and relies on the returned `bool` to gate fund release can be exploited. An attacker who controls the calling contract (or who submits a crafted NEAR transaction to a poorly-written consumer) passes `confirmations = 0`. The light client returns `true` for a Bitcoin transaction that is in the most recent block and has no confirmation depth. That Bitcoin transaction can still be double-spent or orphaned. The consuming contract releases funds based on a false guarantee of finality, resulting in direct loss of value.

### Likelihood Explanation

**Likelihood: Low-to-Medium.** The `verify_transaction_inclusion` functions are public, pausable, and callable by any unprivileged NEAR account. The attacker only needs to supply a valid Merkle proof for a real transaction in any accepted block and set `confirmations = 0`. No privileged keys or special roles are required. The realistic trigger is a consuming bridge contract that does not independently enforce a minimum confirmation count and delegates that responsibility entirely to the light client's return value.

### Recommendation

Add a minimum confirmation guard at the top of `verify_transaction_inclusion`:

```rust
require!(args.confirmations >= 1, "Confirmations must be at least 1");
```

Optionally, enforce a protocol-level minimum (e.g., 6 for Bitcoin mainnet) as a constant, and reject calls below that threshold. Apply the same guard in `verify_transaction_inclusion_v2` before delegating.

### Proof of Concept

1. Deploy the contract with any valid Bitcoin genesis block and `gc_threshold = 100`.
2. Submit one block header via `submit_blocks` (the tip is now at height 1).
3. Call `verify_transaction_inclusion` with:
   - `tx_block_blockhash` = hash of the block at height 1 (the tip, 0 confirmations deep)
   - A valid Merkle proof for any transaction in that block
   - `confirmations = 0`
4. The upper-bound check passes: `0 <= 100`.
5. The depth check evaluates: `(1 - 1) + 1 = 1 >= 0` → passes.
6. The Merkle proof check passes (valid proof supplied).
7. The function returns `true`, certifying a 0-confirmation transaction as confirmed.
8. A consuming bridge contract releases funds against a Bitcoin transaction that has not yet achieved any confirmation depth.

### Citations

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L304-308)
```rust
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
