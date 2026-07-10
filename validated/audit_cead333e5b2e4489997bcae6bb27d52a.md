### Title
Unprivileged Caller Can Invoke `run_mainchain_gc` to Prune Canonical Headers, Breaking SPV Proof Verification — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating contract method with no caller restriction. Any unprivileged NEAR account can call it with an attacker-controlled `batch_size`, immediately pruning mainchain block headers from the contract's storage. This breaks `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` for any block whose header has been removed, causing downstream bridge contracts to fail on otherwise-valid SPV proofs.

---

### Finding Description

The contract enforces caller identity on `submit_blocks` via the `#[trusted_relayer]` attribute, which gates submission to registered, staked relayers. However, `run_mainchain_gc` — the function that permanently deletes block headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height` — carries only a `#[pause]` guard: [1](#0-0) 

The `#[pause]` decorator restricts access only when the contract is administratively paused. When the contract is live (its normal operating state), the function is callable by any NEAR account with no identity check whatsoever.

Compare this to `submit_blocks`, which has both guards: [2](#0-1) 

The `#[trusted_relayer]` attribute is declared at the `impl` block level but is only applied to individual methods that carry the method-level annotation: [3](#0-2) 

`run_mainchain_gc` never receives that annotation, so the relayer gate is never applied to it.

Inside the function, the attacker-controlled `batch_size` is used directly to compute how many headers to delete: [4](#0-3) 

Passing `batch_size = u64::MAX` causes the function to remove every header that exceeds `gc_threshold` in a single call — the maximum possible pruning in one transaction. After deletion, `mainchain_initial_blockhash` is advanced to the new oldest block, making the pruned range permanently unreachable.

---

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both look up the target block in `mainchain_header_to_height`: [5](#0-4) 

If the block's header has been pruned by `run_mainchain_gc`, this lookup panics with `"block does not belong to the current main chain"`. The cross-contract call from a downstream bridge contract fails, and the bridge's release-of-funds logic is never executed. A user who has already sent Bitcoin on-chain receives nothing on NEAR — an irreversible loss of funds.

---

### Likelihood Explanation

The attack requires no special role, no staked deposit, and no leaked key. Any NEAR account can call `run_mainchain_gc` at any time the contract is unpaused. The attacker only needs to observe a pending SPV verification (visible in the NEAR mempool or by monitoring the bridge contract) and front-run it with a GC call that prunes the target block's header. NEAR's deterministic transaction ordering makes this straightforward to time. The attack is repeatable and costs only gas.

---

### Recommendation

Add `#[trusted_relayer]` to `run_mainchain_gc` so that only registered, staked relayers (or accounts with `Role::UnrestrictedRunGC` / `Role::DAO`) can invoke it, consistent with the access model already applied to `submit_blocks`. Alternatively, mark it `#[private]` if external invocation is never intended, or add an explicit `require!(env::predecessor_account_id() == env::current_account_id(), ...)` guard.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 100` and submit 200 headers via a trusted relayer. The mainchain now holds blocks at heights 0–199.
2. A bridge user submits a Bitcoin transaction at height 50 and initiates an SPV proof call to the bridge contract, which will cross-contract-call `verify_transaction_inclusion` for block height 50.
3. Before that cross-contract call executes, the attacker calls:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
   from any unprivileged NEAR account.
4. The function computes `amount_of_headers_we_store = 200`, `total_amount_to_remove = 100`, removes headers at heights 0–99, and advances `mainchain_initial_blockhash` to height 100.
5. The bridge contract's cross-contract call to `verify_transaction_inclusion` for block height 50 now panics: `"block does not belong to the current main chain"`.
6. The bridge contract's promise callback receives a failure, the release of NEAR-side funds is never triggered, and the user's Bitcoin is unrecoverable.

### Citations

**File:** contract/src/lib.rs (L120-126)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
#[near]
impl BtcLightClient {
```

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L391-414)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```
