### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Allowing Bypass of 64-Byte Transaction Forgery Protection - (`contract/src/lib.rs`)

### Summary

The 64-byte transaction Merkle proof forgery protection introduced in `verify_transaction_inclusion_v2` is enforced only in the v2 wrapper, not in the underlying `verify_transaction_inclusion` function. Because `verify_transaction_inclusion` remains a fully public, unrestricted on-chain method, any unprivileged NEAR caller can invoke it directly, bypassing the coinbase Merkle proof validation entirely and obtaining a `true` result for a forged transaction inclusion proof.

### Finding Description

The contract exposes two public verification endpoints:

**v1 (deprecated but still callable):** [1](#0-0) 

The function's own docstring acknowledges the risk:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

**v2 (secure wrapper):** [2](#0-1) 

The coinbase Merkle proof check — the only guard against the 64-byte forgery attack — lives exclusively inside `verify_transaction_inclusion_v2`: [3](#0-2) 

After passing that check, v2 simply delegates to v1: [4](#0-3) 

The `#[deprecated]` Rust attribute is a **compile-time lint only**. It emits a compiler warning to Rust callers but does not restrict the NEAR runtime from dispatching calls to the method. The method selector for `verify_transaction_inclusion` remains registered and callable by any NEAR account. There is no `#[private]`, role guard, or `#[pause(except(roles(...)))]` restriction that would block external callers.

The `ProofArgs` struct accepted by v1 requires no coinbase data: [5](#0-4) 

An attacker constructs a `ProofArgs` with a crafted 64-byte `tx_id` that is actually an internal Merkle tree node hash, supplies a matching `merkle_proof`, and calls `verify_transaction_inclusion` directly. The function computes the Merkle root from the attacker-supplied inputs and compares it to the stored `merkle_root`: [6](#0-5) 

Because no coinbase proof is required, the forgery check is never performed, and the function returns `true` for a transaction that was never included in the block.

### Impact Explanation

Any downstream dApp, bridge, or protocol that calls `verify_transaction_inclusion` (v1) to gate fund releases, cross-chain messages, or other state transitions can be deceived into accepting a fraudulent Bitcoin transaction inclusion proof. The attacker does not need to mine a block or control any privileged role — they only need to craft a valid-looking 64-byte internal node and a compatible Merkle path, which is the well-documented attack described at https://www.bitmex.com/blog/64-Byte-Transactions. The contract returns `true`, and the consuming protocol acts on a lie.

### Likelihood Explanation

The entry path requires zero privileges: any NEAR account can call `verify_transaction_inclusion` with arbitrary `ProofArgs`. The 64-byte forgery technique is publicly documented and has known tooling. Any integrator that has not independently audited the deprecation notice and migrated to v2 is exposed. The structural parallel to the OpenDollar finding is exact: the security mechanism is in the wrapper, not the core, and the core remains reachable.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with a role restriction that prevents external callers. Move the coinbase Merkle proof validation into the shared internal logic so it cannot be bypassed regardless of which public entry point is used. For example:

```rust
// Internal helper enforcing the forgery check unconditionally
fn verify_inclusion_inner(&self, args: ProofArgs, merkle_root: H256) -> bool {
    // coinbase proof must be validated before reaching here
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == merkle_root
}

// v1: make private or remove entirely
#[deprecated]
fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool { ... }

// v2: the only public entry point
pub fn verify_transaction_inclusion_v2(&self, args: ProofArgsV2) -> bool {
    // coinbase check here, then call verify_inclusion_inner
}
```

### Proof of Concept

1. Identify a real Bitcoin block whose `merkle_root` is stored in the contract's `headers_pool`.
2. Using the 64-byte transaction technique (https://www.bitmex.com/blog/64-Byte-Transactions), construct a `tx_id` that is an internal Merkle tree node and a `merkle_proof` path such that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == stored_merkle_root`.
3. Call `verify_transaction_inclusion` directly (bypassing `verify_transaction_inclusion_v2`) with a `ProofArgs` containing the forged `tx_id`, the real `tx_block_blockhash`, the crafted `merkle_proof`, and `tx_index`.
4. The contract returns `true` — confirming inclusion of a transaction that does not exist — because the coinbase proof check in v2 was never executed.

The root cause is that the forgery protection is placed only in the v2 wrapper: [3](#0-2) 

while the core function that performs the actual Merkle comparison remains publicly reachable with no equivalent guard: [7](#0-6)

### Citations

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
