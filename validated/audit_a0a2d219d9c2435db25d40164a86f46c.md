### Title
Caller-Controlled `confirmations = 0` Bypasses Finality Check in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions accept a fully caller-controlled `confirmations` parameter with no contract-enforced minimum. Any unprivileged NEAR account can pass `confirmations = 0`, causing the function to return `true` for a transaction whose containing block has zero additional confirmations (i.e., the current chain tip). This makes every downstream consumer of the verification result vulnerable to double-spend via Bitcoin block reorganization.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` performs the following confirmation check:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

When `args.confirmations = 0`, the left-hand side evaluates to at least `1` for any valid on-chain block (since `saturating_sub` floors at 0 and `+ 1` is always applied), so the condition is **unconditionally satisfied**. The contract imposes no lower bound on the caller-supplied value.

The `confirmations` field is defined in `ProofArgs` as a plain `u64` with no validation constraint:

```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
``` [2](#0-1) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same bypass:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The only guard that exists is an upper-bound check (`args.confirmations <= self.gc_threshold`), which prevents asking for more confirmations than the contract stores — but it does nothing to prevent asking for zero: [4](#0-3) 

---

### Impact Explanation

The purpose of the `confirmations` parameter is to enforce Bitcoin finality before downstream contracts act on a transaction. By passing `confirmations = 0`, a caller receives `true` for a transaction whose block is the current chain tip — a block that has not yet accumulated any additional proof-of-work and is therefore maximally vulnerable to reorganization.

If a downstream NEAR contract (e.g., a bridge, DEX, or custodian) calls `verify_transaction_inclusion` and either:
- allows its own callers to supply the confirmation count, or
- hard-codes `confirmations = 0` or `confirmations = 1`,

then it will release funds or update state based on an unfinalized Bitcoin transaction. If the Bitcoin network subsequently reorganizes that block away, the transaction is gone from the canonical chain while the NEAR-side effect has already been committed — a classic double-spend.

---

### Likelihood Explanation

The entry path is fully unprivileged: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public NEAR contract methods callable by any account with no role restriction. The only prerequisite is that a block containing the target transaction has been submitted to the contract (which a legitimate relayer will do in normal operation). The attacker does not need to be a trusted relayer, does not need any special key, and does not need to submit any block themselves. Bitcoin mainnet reorgs of 1–2 blocks occur occasionally; testnet reorgs are common. The combination of a zero-friction bypass and a realistic reorg probability makes this Medium likelihood.

---

### Recommendation

Introduce a contract-level `min_confirmations: u64` field (set at `init` time and governable via DAO) and enforce it at the start of `verify_transaction_inclusion`:

```rust
require!(
    args.confirmations >= self.min_confirmations,
    "Confirmations below contract minimum"
);
```

This mirrors the correct pattern used in ZetaChain's `ObserveTxIn` path — where the confirmation threshold is enforced by the system, not delegated to the caller.

---

### Proof of Concept

1. A legitimate trusted relayer calls `submit_blocks` and adds block **N** (the new chain tip) to the contract.
2. An attacker (any NEAR account) immediately calls `verify_transaction_inclusion` with:
   - `tx_block_blockhash` = hash of block **N**
   - a valid Merkle proof for a transaction in block **N**
   - `confirmations = 0`
3. The check evaluates as `(N − N) + 1 = 1 ≥ 0` → **passes**. The function returns `true`.
4. A downstream NEAR bridge contract, having received `true`, releases the corresponding funds to the attacker on NEAR.
5. The Bitcoin network reorganizes block **N** away (e.g., a competing miner's chain wins).
6. The transaction no longer exists on the canonical Bitcoin chain, but the NEAR-side funds have already been released — **double spend achieved**. [5](#0-4)

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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** btc-types/src/contract_args.rs (L16-25)
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
