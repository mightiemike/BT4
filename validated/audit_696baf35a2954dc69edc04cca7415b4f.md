### Title
Unrestricted Access on `run_mainchain_gc` Allows Any Caller to Prematurely Purge Canonical Block Headers — (`File: contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating NEAR contract function that permanently deletes canonical block headers and advances `mainchain_initial_blockhash`. It carries no caller access control: any unprivileged NEAR account can invoke it at will. An attacker can trigger maximum-batch GC at any time, destroying headers that downstream consumers still need for transaction inclusion verification and chain reorg resolution.

---

### Finding Description

`run_mainchain_gc` is decorated only with `#[pause(except(roles(Role::UnrestrictedRunGC)))]`: [1](#0-0) 

The `#[pause]` macro exclusively governs behavior when the contract is **paused** — it allows `UnrestrictedRunGC` role holders to bypass the pause gate. When the contract is **not paused** (the normal production state), the macro imposes zero caller restriction. Any NEAR account, with no role or stake, can call `run_mainchain_gc(batch_size)`.

Contrast this with `submit_blocks`, which carries both `#[pause]` **and** `#[trusted_relayer]`: [2](#0-1) 

The `#[trusted_relayer]` guard — applied at the `impl` block level and explicitly attached to `submit_blocks` — enforces that only registered, staked relayers may call that function: [3](#0-2) 

`run_mainchain_gc` has no equivalent guard. Inside the function, when `amount_of_headers_we_store > gc_threshold`, it removes up to `min(total_amount_to_remove, batch_size)` headers from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, then permanently advances `mainchain_initial_blockhash`: [4](#0-3) 

The `remove_block_header` helper irrecoverably deletes entries from both `mainchain_header_to_height` and `headers_pool`: [5](#0-4) 

---

### Impact Explanation

**Broken transaction inclusion proofs.** `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height` and `headers_pool`. If an attacker has GC'd those entries, both functions panic with `"block does not belong to the current main chain"` or `"cannot find requested transaction block"`, making all SPV proofs for affected blocks permanently unverifiable: [6](#0-5) 

**Blocked chain reorgs.** The project's own documentation states: *"If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor."* [7](#0-6) 

An attacker who GC's headers just before a legitimate fork submission forces the reorg path to panic, permanently blocking the fork from being promoted even if it has more chainwork.

**Corrupted `mainchain_initial_blockhash`.** After deletion, `mainchain_initial_blockhash` is advanced to `end_removal_height`, a state mutation that cannot be reversed without a migration. This corrupts `get_mainchain_size` and `get_last_n_blocks_hashes` for all downstream consumers.

---

### Likelihood Explanation

The entry path requires no privilege, no stake, and no special knowledge — only the ability to send a NEAR transaction. The function is publicly documented in `CLAUDE.md` as manually triggerable. The condition `amount_of_headers_we_store > gc_threshold` is routinely satisfied in production (the recommended `gc_threshold` is 52,704 blocks, and the chain grows continuously). An attacker simply waits for the chain to exceed the threshold and calls `run_mainchain_gc(u64::MAX)` to delete the maximum allowed batch in one transaction.

---

### Recommendation

Add `#[trusted_relayer]` to `run_mainchain_gc`, mirroring the guard already applied to `submit_blocks`. Alternatively, restrict callers using an explicit role check (e.g., `Role::DAO` or a new dedicated `GCManager` role) via `near_plugins::AccessControllable::acl_is_caller_role`. The `#[pause]` attribute alone is not a caller restriction and must not be relied upon as one.

---

### Proof of Concept

```rust
// Any unprivileged NEAR account can execute this:
let outcome = any_account
    .call(contract.id(), "run_mainchain_gc")
    .args_json(json!({"batch_size": 100_000}))
    .max_gas()
    .transact()
    .await?;
// Succeeds with no role check when contract is unpaused.
// Oldest (amount_of_headers_we_store - gc_threshold) headers are permanently deleted.
// mainchain_initial_blockhash is advanced.
// Subsequent verify_transaction_inclusion calls for deleted blocks panic.
assert!(outcome.is_success());
```

The test suite itself demonstrates that `run_mainchain_gc` is callable by an ordinary `user_account` with no role grants: [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-313)
```rust
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

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```

**File:** contract/tests/test_basics.rs (L515-521)
```rust
        let outcome = user_account
            .call(contract.id(), "run_mainchain_gc")
            .args_json(json!({"batch_size": 100}))
            .max_gas()
            .transact()
            .await?;
        assert!(outcome.is_success());
```
