### Title
Unrestricted Access in `BtcLightClient::run_mainchain_gc()` Allows Any Caller to Prune Canonical Chain Headers — (`contract/src/lib.rs`)

### Summary
`run_mainchain_gc` is a public, state-mutating function with no caller restriction. Any unprivileged NEAR account can invoke it with an arbitrary `batch_size`, permanently deleting mainchain block headers from contract storage and advancing `mainchain_initial_blockhash`. This corrupts the canonical chain state and breaks SPV proof verification for any transaction whose block header has been pruned.

### Finding Description
The function is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

The `#[pause]` attribute controls whether the function is callable when the contract is **paused** — it places no restriction on who may call it when the contract is **unpaused**. The `Role::UnrestrictedRunGC` role only grants the ability to bypass the pause gate; it is not a required role for calling the function. There is no `require!(acl_has_role(...))` or equivalent guard.

When called, the function removes the oldest mainchain headers from `headers_pool` and `mainchain_height_to_header`, then advances `mainchain_initial_blockhash` to the new oldest block: [2](#0-1) 

The amount removed per call is bounded by `amount_of_headers_we_store - gc_threshold`, so the attacker cannot prune below the `gc_threshold` floor in a single call. However, as the relayer continuously submits new blocks and the chain grows, the attacker can repeatedly call `run_mainchain_gc` to keep the window perpetually trimmed to exactly `gc_threshold`, racing ahead of legitimate GC scheduling.

### Impact Explanation
- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height`. If the attacker has pruned that block, the call panics with `"block does not belong to the current main chain"`, permanently breaking SPV proofs for any transaction in a pruned block.
- Chain reorg logic in `reorg_chain` walks back through `headers_pool` to find the common ancestor. If the attacker has pruned headers near the fork point, the reorg panics with `"PrevBlockNotFound"`, as explicitly noted in the project documentation.
- `mainchain_initial_blockhash` is permanently advanced, so the damage is irreversible without a contract migration.

**Impact: Medium** — Canonical chain state is corrupted; SPV proofs and reorg resolution fail for affected block ranges.

### Likelihood Explanation
The entry path is a direct, zero-cost NEAR contract call from any account. No staking, no role, no deposit is required. The attacker only needs to observe when `get_mainchain_size()` exceeds `gc_threshold` (a public view call) and then call `run_mainchain_gc` with a large `batch_size`. This is trivially automatable.

**Likelihood: Medium** — Requires the chain to have grown beyond `gc_threshold`, which is a normal operational condition.

### Recommendation
Add a role-based access control guard to `run_mainchain_gc` so that only accounts holding a designated role (e.g., `Role::DAO` or a new `GCManager` role) may invoke it directly. The `#[pause]` attribute is not a substitute for caller authorization. For example:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::DAO, &env::predecessor_account_id())
            || self.acl_has_role(Role::RelayerManager, &env::predecessor_account_id()),
        "Unauthorized: caller lacks GC permission"
    );
    // ...
}
```

Alternatively, make the function `#[private]` and expose GC only as an internal step within `submit_blocks`, which is already gated by `#[trusted_relayer]`. [3](#0-2) 

### Proof of Concept
1. Relayer submits enough blocks so that `get_mainchain_size()` returns a value greater than `gc_threshold` (e.g., `gc_threshold + 500`).
2. Attacker calls `run_mainchain_gc({ "batch_size": 500 })` from any unprivileged account.
3. The contract removes 500 mainchain headers, advancing `mainchain_initial_blockhash` by 500 blocks.
4. Any downstream consumer calling `verify_transaction_inclusion_v2` for a transaction in one of those 500 pruned blocks now receives a panic: `"block does not belong to the current main chain"`.
5. Any fork whose common ancestor falls within the pruned range causes `reorg_chain` to panic with `"PrevBlockNotFound"`, halting chain progression.

The integration test at `contract/tests/test_basics.rs:515-521` demonstrates that `run_mainchain_gc` is callable by an ordinary `user_account` with no role grants, confirming the unrestricted access path. [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L166-181)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L388-393)
```rust
        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L401-414)
```rust
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
