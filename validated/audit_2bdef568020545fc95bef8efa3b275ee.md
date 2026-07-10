### Title
Unvalidated `gc_threshold = 0` in `init` Permanently Disables Confirmation-Depth Enforcement in SPV Proof Verification — (`File: contract/src/lib.rs`)

### Summary

The `init` function stores `args.gc_threshold` directly into contract state without any non-zero validation. There is no setter for `gc_threshold` after initialization. If the deployer passes `gc_threshold = 0`, the contract is permanently misconfigured: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will reject every call with `confirmations > 0` and silently accept every call with `confirmations = 0`, making the confirmation-depth check trivially true for any block. The contract must be redeployed to recover.

---

### Finding Description

In `contract/src/lib.rs`, the `init` function assigns `gc_threshold` directly from the caller-supplied argument with no validation:

```rust
// contract/src/lib.rs line 143
gc_threshold: args.gc_threshold,
``` [1](#0-0) 

There is no `require!(args.gc_threshold > 0, ...)` guard anywhere in `init` or `init_genesis`. A grep across the entire repository confirms there is no setter for `gc_threshold` after initialization — the only assignment is the one above.

When `gc_threshold = 0` is stored, `verify_transaction_inclusion` enforces:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [2](#0-1) 

With `gc_threshold = 0`, this `require!` passes only when `args.confirmations == 0`. The subsequent confirmation-depth check:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [3](#0-2) 

…becomes `… >= 0`, which is trivially true for every block in the chain. The function then proceeds to evaluate only the Merkle proof, returning `true` for any transaction in any block regardless of how recently it was mined.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase-proof check, so it is equally affected:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [4](#0-3) 

The `InitArgs` struct exposes `gc_threshold` as a plain `u64` with no documented lower bound enforced on-chain:

```rust
pub struct InitArgs {
    ...
    pub gc_threshold: u64,
    ...
}
``` [5](#0-4) 

---

### Impact Explanation

Any downstream contract or application that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` and relies on `confirmations > 0` to guard against double-spend attacks will receive a permanent panic/rejection — the contract will never accept a proof with any non-zero confirmation requirement. Callers that pass `confirmations = 0` will receive a result that is based solely on the Merkle proof, with zero confirmation-depth protection. The SPV security invariant — that a transaction must be buried under N confirmed blocks before being trusted — is permanently destroyed. There is no privileged call to update `gc_threshold`; the contract must be redeployed.

---

### Likelihood Explanation

The `init` function is marked `#[private]`, so only the contract's own account (the deployer) can call it. The misconfiguration requires the deployer to pass `gc_threshold = 0`, either accidentally (e.g., omitting the field in a JSON call, which defaults to `0` for `u64`) or through a scripting error. The relayer's example config (`relayer/config.toml.example`) sets `gc_threshold` to a non-zero value, but there is no on-chain enforcement. Given that JSON deserialization of a missing `u64` field silently defaults to `0`, a deployment script that omits the field is a realistic trigger.

---

### Recommendation

Add a non-zero guard for `gc_threshold` inside `init`, before the field is stored:

```rust
require!(args.gc_threshold > 0, "gc_threshold must be greater than zero");
```

This should be placed immediately before or after the `acl_init_super_admin` call in `init`, analogous to the recommendation in the external report to validate constructor parameters before storing them. [6](#0-5) 

---

### Proof of Concept

1. Deploy the contract with `InitArgs { gc_threshold: 0, ... }` (or omit `gc_threshold` from the JSON call, causing it to default to `0`).
2. Submit any valid block header via `submit_blocks`.
3. Call `verify_transaction_inclusion` with `confirmations: 1` for a transaction in the just-submitted block (zero blocks of confirmation). The call panics with `"The required number of confirmations exceeds the number of blocks stored in memory"` — permanently, for every caller requiring any confirmation depth.
4. Call `verify_transaction_inclusion` with `confirmations: 0` for the same transaction. The call succeeds and returns `true` based on the Merkle proof alone, with no confirmation-depth check applied — permanently bypassing double-spend protection for all future callers.
5. There is no privileged method to update `gc_threshold`. The contract must be redeployed.

### Citations

**File:** contract/src/lib.rs (L135-145)
```rust
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };
```

**File:** contract/src/lib.rs (L149-158)
```rust
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );
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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** btc-types/src/contract_args.rs (L7-14)
```rust
pub struct InitArgs {
    pub genesis_block_hash: H256,
    pub genesis_block_height: u64,
    pub skip_pow_verification: bool,
    pub gc_threshold: u64,
    pub network: Network,
    pub submit_blocks: Vec<Header>,
}
```
