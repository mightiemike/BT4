### Title
Unprivileged Caller Can Permanently Delete Mainchain Headers via `run_mainchain_gc()` — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc()` carries only a `#[pause]` guard, which restricts calls only when the contract is paused. When the contract is in normal (unpaused) operation, **any unprivileged NEAR account** can call it with an arbitrary `batch_size`. This allows an attacker to permanently delete mainchain block headers from storage and corrupt `mainchain_initial_blockhash`, causing `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` to permanently fail for any block that was removed.

---

### Finding Description

`run_mainchain_gc` is declared as a public `#[near]` method with only a pause guard: [1](#0-0) 

There is no `#[trusted_relayer]` attribute, no role check, and no `#[private]` guard. The `#[pause(except(roles(Role::UnrestrictedRunGC)))]` macro only blocks callers when the contract is paused; during normal operation it imposes no restriction whatsoever. Any NEAR account — with zero stake, no role, and no deposit — can call:

```
run_mainchain_gc(batch_size: u64)
```

The function computes how many headers exceed `gc_threshold` and removes up to `batch_size` of the oldest mainchain headers: [2](#0-1) 

Each removed header is deleted from both `mainchain_height_to_header` and `headers_pool`, and `mainchain_initial_blockhash` is advanced to the new oldest block: [3](#0-2) 

These deletions are **irreversible**. The contract provides no mechanism to re-insert a GC'd mainchain header. The `submit_blocks` path only appends new headers extending the current tip; it cannot restore pruned historical entries.

By contrast, `submit_blocks` — the only other state-mutating entry point — is correctly guarded: [4](#0-3) 

The design intent documented in `contract/CLAUDE.md` confirms that `run_mainchain_gc` is meant to be callable manually, but no access control was applied to restrict who may trigger it: [5](#0-4) 

---

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` both require the target block to be present in `mainchain_header_to_height`: [6](#0-5) 

If an attacker has already GC'd that block, the lookup returns `None` and the call panics with `"block does not belong to the current main chain"`. Because the deletion is permanent, **no future call can ever succeed for that block**. Any downstream NEAR contract or user relying on SPV proof verification for a pruned block is permanently denied service.

Additionally, `CLAUDE.md` explicitly warns that GC of blocks near a fork point causes `reorg_chain` to panic with `PrevBlockNotFound`: [7](#0-6) 

An attacker who triggers GC at a strategically chosen moment can therefore also prevent legitimate chain reorganizations from completing.

---

### Likelihood Explanation

The attack requires no special privilege, no deposit, and no cryptographic material. Any NEAR account can call `run_mainchain_gc` at any time the contract is not paused. The attacker only needs to wait until the mainchain size exceeds `gc_threshold` (which happens naturally during normal relayer operation) and then call the function with `batch_size = u64::MAX` to remove the maximum number of headers in a single transaction. Front-running a pending `verify_transaction_inclusion` call is straightforward on NEAR because transaction ordering within a block is observable.

---

### Recommendation

Add a `#[trusted_relayer]` attribute (matching the guard on `submit_blocks`) or an explicit role check (e.g., `Role::DAO` or `Role::RelayerManager`) to `run_mainchain_gc` so that only authorized accounts can trigger manual GC. The internal call from `submit_blocks` already bypasses the guard correctly since it is a Rust-level method call, not a cross-contract call.

---

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704`.
2. Wait (or submit blocks) until the mainchain contains more than `52704` headers.
3. From any unprivileged NEAR account (no role, no deposit), call:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
4. The contract removes all headers beyond `gc_threshold`, permanently updating `mainchain_initial_blockhash`.
5. Any subsequent call to `verify_transaction_inclusion` for a block that was removed now panics with `"block does not belong to the current main chain"`, permanently blocking SPV proof verification for those blocks.

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
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

**File:** contract/CLAUDE.md (L39-39)
```markdown
- **gc_threshold**: max number of mainchain blocks to keep in storage. When the mainchain grows beyond this, the oldest mainchain blocks are pruned. GC runs automatically during `submit_blocks()` (with `batch_size` = number of submitted headers) and can also be triggered manually via `run_mainchain_gc(batch_size)`. Only mainchain blocks are deleted; fork/sidechain blocks are not affected
```

**File:** contract/CLAUDE.md (L60-60)
```markdown
**Caveat**: If mainchain blocks near the fork point have been garbage collected, reorg will fail — the contract panics with `PrevBlockNotFound` when it cannot walk the chain back to the common ancestor. This means GC depth must be set conservatively relative to expected fork lengths
```
