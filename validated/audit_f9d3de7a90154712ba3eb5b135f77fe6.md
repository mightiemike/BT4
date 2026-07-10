### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the 64-Byte Merkle Forgery Mitigation Introduced in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but remains a fully public, unrestricted NEAR entry point. Any unprivileged caller can invoke it directly, bypassing the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` was specifically introduced to enforce. Downstream contracts that gate fund releases on a `true` result from this light client can be deceived into accepting a forged proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the well-known 64-byte Bitcoin Merkle tree second-preimage attack. It enforces two guards before delegating to v1:

1. **Proof-depth parity**: `merkle_proof.len() == coinbase_merkle_proof.len()` — ensures the target transaction sits at the same tree depth as the coinbase, ruling out internal-node forgeries.
2. **Coinbase proof verification**: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == header.block_header.merkle_root` — anchors the depth check to a real, verifiable transaction. [1](#0-0) 

The deprecated v1 function applies **neither guard**. It only checks that `merkle_proof` is non-empty, then computes the Merkle root and compares it to the stored header root: [2](#0-1) 

Critically, v1 is declared `pub` with no access-control attribute beyond `#[pause]`. The `#[deprecated]` Rust attribute is a **compiler lint only** — it emits a warning to Rust callers but does not restrict on-chain invocation. Any NEAR account can call `verify_transaction_inclusion` directly as a contract method, completely skipping v2's coinbase validation.

The contract's own internal documentation acknowledges the attack surface explicitly:

> "This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash." [3](#0-2) 

The function's own NatSpec repeats the same warning: [4](#0-3) 

The relayer client also retains a live binding to the v1 method name, confirming it is treated as a production-reachable endpoint: [5](#0-4) 

---

### Impact Explanation

The 64-byte forgery works as follows: Bitcoin's Merkle tree is built by hashing pairs of transaction IDs. Any internal node `H(left || right)` is itself a 32-byte value that can be supplied as a `tx_id`. An attacker who knows the Merkle tree structure of a real, confirmed block can:

- Pick any internal node `N` at depth `d`.
- Construct a valid `merkle_proof` of length `d` that walks from `N` up to the stored `merkle_root`.
- Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index` matching `N`'s position, and the crafted proof.

Because v1 only checks `compute_root_from_merkle_proof(tx_id, tx_index, proof) == stored_merkle_root`, and this equation holds for internal nodes by construction, the function returns `true` for a transaction that **does not exist**.

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a fund release (e.g., a bridge, escrow, or cross-chain settlement contract) will accept the forged proof and release funds to the attacker. [6](#0-5) 

---

### Likelihood Explanation

- **No privilege required**: `verify_transaction_inclusion` is callable by any NEAR account with no role, stake, or whitelist requirement.
- **Inputs are fully attacker-controlled**: `tx_id`, `tx_index`, `tx_block_blockhash`, and `merkle_proof` are all supplied by the caller.
- **Merkle tree data is public**: Bitcoin block Merkle trees are fully public; computing internal nodes requires only standard Bitcoin RPC calls.
- **The attack is well-documented**: The 64-byte forgery is a published, understood vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). Exploit tooling exists.
- **The only prerequisite** is that a downstream contract consuming this light client's v1 result exists — which is the entire purpose of the light client's verification API.

---

### Recommendation

**Remove `verify_transaction_inclusion` from the public ABI entirely.** Since `verify_transaction_inclusion_v2` already calls v1 internally as a private Rust method call, the fix is to change the visibility of v1 from `pub fn` to `fn` (private). This preserves the internal delegation chain while closing the direct on-chain bypass path.

If removal is not immediately feasible, add an explicit `#[private]` attribute or an `access_control` guard so that only the contract itself (i.e., internal cross-contract calls) can invoke it. All external callers and downstream integrators must be migrated to `verify_transaction_inclusion_v2`. [7](#0-6) 

---

### Proof of Concept

**Setup**: A downstream bridge contract calls `verify_transaction_inclusion` on this light client and releases funds when it returns `true`.

**Attack**:

1. Attacker selects any confirmed Bitcoin block already stored in the light client's `headers_pool`.
2. Using standard Bitcoin RPC (`getblock`), attacker reconstructs the full Merkle tree and picks an internal node `N` at depth `d ≥ 1` (e.g., the parent of the coinbase and tx[1]).
3. Attacker computes a valid `merkle_proof` of length `d` from `N` to the block's `merkle_root`.
4. Attacker calls `verify_transaction_inclusion` directly on the NEAR contract:
   ```
   tx_id             = N          (internal node, not a real txid)
   tx_block_blockhash = <real confirmed block hash>
   tx_index          = <position of N in its level>
   merkle_proof      = [sibling_0, sibling_1, ..., sibling_{d-1}]
   confirmations     = 1
   ```
5. `compute_root_from_merkle_proof(N, index, proof)` reconstructs the correct `merkle_root` → function returns `true`.
6. The downstream bridge contract receives `true`, treats the forged `tx_id` as a proven Bitcoin transaction, and releases funds to the attacker.

The v2 function would have rejected this call at step 5 because no valid `coinbase_merkle_proof` of the same length `d` can be provided for a fake coinbase at depth `d` that also satisfies `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_proof) == merkle_root` unless the attacker controls the block — which they do not. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
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

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```

**File:** relayer/src/near_client.rs (L32-32)
```rust
const VERIFY_TRANSACTION_INCLUSION: &str = "verify_transaction_inclusion";
```
