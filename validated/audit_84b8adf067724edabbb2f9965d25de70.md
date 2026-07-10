### Title
Missing Minimum Confirmations Enforcement Allows Zero-Confirmation Transaction Verification - (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` enforce only an **upper** bound on `confirmations` (must not exceed `gc_threshold`) but impose **no lower bound**. Passing `confirmations = 0` causes the depth check to trivially pass for any mainchain block, including the current tip. This is the direct analog of `amountOutMin = 0`: a missing minimum bound that any caller can exploit to receive a maximally permissive result.

---

### Finding Description

In `verify_transaction_inclusion` the only guard on the caller-supplied `confirmations` value is:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

No lower bound is checked. The subsequent depth check is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

Because `confirmations` is `u64`, the expression `<u64 value> >= 0` is always `true`. When `confirmations = 0` is supplied, this `require!` never fires, and the function returns `true` for any transaction whose block appears anywhere in the current mainchain — including the tip block with zero blocks built on top of it.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

Both functions are decorated only with `#[pause]` — no `#[trusted_relayer]` or role guard — so any unprivileged NEAR account can call them while the contract is unpaused. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Any downstream contract (bridge, cross-chain protocol, escrow) that calls `verify_transaction_inclusion_v2` and either (a) passes `confirmations = 0` itself, or (b) forwards a caller-supplied `confirmations` value without enforcing a minimum, will receive `true` for a transaction that sits at the very tip of the chain with no additional blocks confirming it. Such a transaction is trivially reversible by a Bitcoin reorg. An attacker can:

1. Broadcast a Bitcoin transaction and wait for it to appear in one block.
2. Once the relayer submits that block to the light client, immediately call `verify_transaction_inclusion_v2` with `confirmations = 0`.
3. Receive `true` and claim the corresponding NEAR-side asset (e.g., wrapped BTC, bridge payout).
4. Simultaneously attempt to double-spend the Bitcoin transaction on the Bitcoin side.

The corrupted invariant is the contract's guarantee that a verified transaction has at least N blocks of PoW security behind it. With `confirmations = 0` that guarantee is vacuous.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion_v2`. The attacker does not need to submit Bitcoin PoW themselves — they only need to wait for the honest relayer to submit a block containing their transaction, which happens automatically. The only prerequisite is a downstream contract that does not independently enforce a minimum confirmations floor, which is a realistic deployment scenario given that the light client's API appears to be the authoritative source of confirmation semantics.

---

### Recommendation

Enforce a protocol-level minimum on `confirmations` inside `verify_transaction_inclusion`, for example:

```rust
const MIN_CONFIRMATIONS: u64 = 1; // or a configurable setter

require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    "Confirmations must be at least MIN_CONFIRMATIONS"
);
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

Optionally expose a `set_min_confirmations` setter gated behind a DAO or manager role so the floor can be adjusted without a contract upgrade.

---

### Proof of Concept

1. Deploy the contract and initialize it with a genesis block.
2. Have the relayer submit one Bitcoin block containing transaction `tx_id` at height `H`.
3. As an unprivileged NEAR account, call:
   ```json
   verify_transaction_inclusion_v2({
     "tx_id": "<tx_id>",
     "tx_block_blockhash": "<block_hash_at_H>",
     "tx_index": 0,
     "merkle_proof": [...],
     "coinbase_tx_id": "<coinbase_tx_id>",
     "coinbase_merkle_proof": [...],
     "confirmations": 0
   })
   ```
4. The function returns `true` immediately, with the tip at height `H` and `target_block_height = H`, because `(H - H) + 1 = 1 >= 0` always holds.
5. The transaction has zero blocks of confirmation depth; a Bitcoin reorg of depth 1 would invalidate it, but the light client has already certified it as verified. [2](#0-1) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

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
