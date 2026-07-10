### Title
Caller Can Bypass Confirmation Requirement by Passing `confirmations = 0` in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`) only upper-bounds the caller-supplied `confirmations` parameter against `gc_threshold`, but never enforces a minimum of 1. Passing `confirmations = 0` makes the confirmation-depth check trivially true for every mainchain block, including the chain tip (0 confirmations). Any downstream contract that gates a financial action on this result can be tricked into accepting an unconfirmed — and potentially double-spendable — transaction as fully settled.

---

### Finding Description

The only guard on `confirmations` is an upper-bound check:

```rust
// lib.rs lines 289-292
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

The actual depth check that follows is:

```rust
// lib.rs lines 304-308
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

Because `confirmations` is `u64`, when the caller supplies `0` the right-hand side is `0`, and the left-hand side (also `u64`) is always `>= 0`. The check is unconditionally satisfied. The function then verifies the Merkle proof and returns `true` for any transaction in any mainchain block — including the very tip — regardless of how recently it was submitted.

`verify_transaction_inclusion_v2` inherits the same flaw because it delegates to `verify_transaction_inclusion` after its own coinbase-proof check:

```rust
// lib.rs lines 367-368
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `ProofArgs` struct accepts `confirmations` as a plain `u64` with no enforced lower bound:

```rust
// contract_args.rs lines 22-24
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
``` [4](#0-3) 

---

### Impact Explanation

The confirmation depth is the sole on-chain mechanism that protects downstream consumers from accepting transactions that could be erased by a chain reorganization. With `confirmations = 0`, the protection is completely removed. A downstream contract (e.g., a bridge or escrow) that calls `verify_transaction_inclusion` and releases funds upon a `true` result can be exploited: an attacker submits a BTC transaction, immediately calls the verifier with `confirmations = 0` before any reorganization risk has passed, triggers the fund release, and then double-spends the BTC transaction on the source chain. The corrupted value is the proof result (`true`) returned for a block at the chain tip that has not yet achieved any finality depth.

---

### Likelihood Explanation

`verify_transaction_inclusion` is a public, permissionless view function — any NEAR account can call it with arbitrary arguments at zero cost. Any downstream contract that forwards a user-supplied `confirmations` value (or that itself passes `0`) is immediately exploitable. The attack requires no privileged role, no leaked key, and no social engineering.

---

### Recommendation

Enforce a minimum of 1 confirmation at the top of `verify_transaction_inclusion`, before any other logic:

```rust
require!(args.confirmations >= 1, "confirmations must be at least 1");
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [5](#0-4) 

---

### Proof of Concept

1. A BTC transaction `tx_id` is included in the current mainchain tip block (`tx_block_blockhash = mainchain_tip_blockhash`).
2. The attacker (or a downstream contract accepting user-supplied parameters) calls:
   ```
   verify_transaction_inclusion({
       tx_id,
       tx_block_blockhash,   // tip block — 0 confirmations
       tx_index,
       merkle_proof,
       confirmations: 0,     // bypasses depth check
   })
   ```
3. Line 290-292: `0 <= gc_threshold` — passes.
4. Line 304-308: `(tip_height - tip_height) + 1 = 1 >= 0` — passes trivially.
5. Lines 318-322: Merkle proof is valid — function returns `true`.
6. Downstream contract releases funds.
7. Attacker double-spends the BTC transaction on the source chain before the block achieves sufficient depth. [6](#0-5)

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
