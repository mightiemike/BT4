### Title
Unprivileged Caller Can Invoke `run_mainchain_gc` to Permanently Prune Canonical Headers and Invalidate SPV Proofs — (File: `contract/src/lib.rs`)

---

### Summary

`run_mainchain_gc` is a public, state-mutating function that carries no relayer or role guard. Any unprivileged NEAR account can call it directly with `batch_size = u64::MAX`, atomically removing every header eligible for garbage collection, permanently advancing `mainchain_initial_blockhash`, and causing `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to panic for any block whose entry was erased.

---

### Finding Description

`submit_blocks` is correctly gated behind `#[trusted_relayer]`, ensuring only whitelisted relayers can mutate the canonical chain. [1](#0-0) 

Internally, `submit_blocks` calls `run_mainchain_gc` with a small `batch_size` equal to the number of headers just submitted, keeping GC incremental and relayer-controlled. [2](#0-1) 

`run_mainchain_gc` itself, however, carries only a `#[pause]` attribute — no `#[trusted_relayer]`, no role check beyond `UnrestrictedRunGC` (which is a bypass for the pause, not a restriction). It is fully reachable by any unprivileged NEAR account when the contract is not paused. [3](#0-2) 

When called with `batch_size = u64::MAX`, the expression `std::cmp::min(total_amount_to_remove, batch_size)` resolves to `total_amount_to_remove`, so every header beyond `gc_threshold` is deleted in a single transaction. [4](#0-3) 

For each removed height the function calls `remove_block_header`, which erases the entry from both `mainchain_header_to_height` and `headers_pool`, then advances `mainchain_initial_blockhash` to the new oldest block. [5](#0-4) 

---

### Impact Explanation

After the attacker's call, any subsequent invocation of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a block whose hash was erased will unconditionally panic at the `mainchain_header_to_height` lookup: [6](#0-5) 

The confirmation-count guard (`args.confirmations <= self.gc_threshold`) does **not** protect against this: it only checks the requested confirmation depth, not whether the target block still exists in the stored window. [7](#0-6) 

The result is permanent, irreversible corruption of the canonical-chain mapping: `mainchain_initial_blockhash` is advanced without any new blocks being submitted, headers are deleted from `headers_pool`, and every consumer contract that calls `verify_transaction_inclusion` for an affected block receives a hard panic rather than a `false` return. The broken invariant is that `mainchain_initial_blockhash` must only advance as a side-effect of a trusted-relayer-gated `submit_blocks` call.

---

### Likelihood Explanation

**Medium.** The entry point requires no special privilege — any funded NEAR account can call `run_mainchain_gc`. The precondition (mainchain length exceeding `gc_threshold`) is met in normal production operation once the chain has been running for roughly one GC cycle. The attacker pays only standard NEAR gas; no staking, key compromise, or social engineering is required.

---

### Recommendation

Apply the same `#[trusted_relayer]` macro to `run_mainchain_gc` that protects `submit_blocks`, or introduce an explicit role check (e.g., `Role::DAO` or `Role::RelayerManager`) so that direct external invocation is restricted to authorized accounts. The internal call from `submit_blocks` already passes through the trusted-relayer gate, so adding the guard to `run_mainchain_gc` does not break the intended relayer flow.

---

### Proof of Concept

1. Deploy the contract on NEAR testnet with `gc_threshold = 100`.
2. Submit 200 block headers via a trusted relayer so that `amount_of_headers_we_store = 200 > 100`.
3. From **any** unprivileged NEAR account (no role, no stake), call:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
4. Observe that all 100 GC-eligible headers are deleted atomically and `mainchain_initial_blockhash` is advanced to block 101.
5. Call `verify_transaction_inclusion` for any block in the range [1, 100]. The contract panics with `"block does not belong to the current main chain"` — SPV proofs for those blocks are permanently invalidated.
6. Confirm that the same call from a non-relayer account to `submit_blocks` is rejected, demonstrating the asymmetric access control gap on `run_mainchain_gc`.

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L175-181)
```rust
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L289-292)
```rust
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );
```

**File:** contract/src/lib.rs (L299-301)
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

**File:** contract/src/lib.rs (L391-393)
```rust
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
