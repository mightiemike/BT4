### Title
Zero-Confirmation Proof Accepted Without Guard — (`File: contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept `confirmations = 0` from any unprivileged NEAR caller. When `confirmations` is zero, every confirmation guard in the function passes trivially, causing the function to return `true` for a transaction in any mainchain block regardless of how recently it was submitted. A downstream contract consuming this result treats the transaction as finalized when it has no burial depth at all.

### Finding Description

`ProofArgs.confirmations` is a plain `u64` with no minimum constraint. [1](#0-0) 

Inside `verify_transaction_inclusion`, the only upper-bound guard is: [2](#0-1) 

When `confirmations = 0` this check is `0 <= gc_threshold`, which is always true. The confirmation-depth guard that follows is: [3](#0-2) 

`saturating_sub` returns a `u64`, so the left-hand side is always `≥ 0`. Adding 1 makes it `≥ 1`. The condition `1 >= 0` is always true, so the guard is completely bypassed. The function then proceeds to compute and return the Merkle proof result as `true` for any block that is in the mainchain, with no confirmation depth enforced.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase check, so it inherits the same flaw: [4](#0-3) 

The `confirmations` field is forwarded unchanged through `From<ProofArgsV2> for ProofArgs`: [5](#0-4) 

### Impact Explanation

A downstream NEAR contract (e.g., a Bitcoin bridge or cross-chain swap) that calls `verify_transaction_inclusion_v2` with `confirmations = 0` receives `true` for a transaction that has zero burial depth. Bitcoin has probabilistic finality; a block with no confirmations can be reorganized away. The downstream contract has already acted on a proof result that the light client certified as valid, but the underlying Bitcoin transaction may never achieve finality. The corrupted value is the proof result itself — `true` is returned for a transaction that the protocol has not confirmed at all.

### Likelihood Explanation

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, permissionless NEAR entry points gated only by `#[pause]`. [6](#0-5) [7](#0-6) 

Any unprivileged NEAR account can call them with `confirmations = 0`. A downstream bridge contract that does not independently enforce a minimum confirmation count — a reasonable assumption given that the light client is the authoritative source of Bitcoin finality on NEAR — is directly exploitable. The attack requires no privileged access, no leaked keys, and no social engineering.

### Recommendation

Add an explicit lower-bound guard at the top of `verify_transaction_inclusion`:

```rust
require!(args.confirmations >= 1, "Confirmations must be at least 1");
```

This mirrors the fix applied to the Moeda crowdsale: just as `finalize()` was patched to require that either the cap or the end block had been reached before the state transition was allowed, `verify_transaction_inclusion` must require that at least one confirmation has been reached before certifying a proof result.

### Proof of Concept

1. A relayer submits block `B` containing transaction `T` to the light client via `submit_blocks`. Block `B` is now the mainchain tip with height `H`.
2. An attacker (or any NEAR account) immediately calls `verify_transaction_inclusion_v2` with:
   - `tx_block_blockhash = B`
   - valid Merkle proof for `T`
   - `confirmations = 0`
3. Inside the function:
   - `0 <= gc_threshold` → passes
   - `(H - H) + 1 = 1 >= 0` → passes
   - Merkle proof is valid → function returns `true`
4. A downstream bridge contract receives `true` and releases NEAR-side funds.
5. The Bitcoin miner (or attacker with sufficient hashrate) reorganizes block `B` away.
6. The NEAR-side funds have already been released against a Bitcoin transaction that no longer exists in the canonical chain. [8](#0-7)

### Citations

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
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

**File:** contract/src/lib.rs (L287-323)
```rust
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

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```
