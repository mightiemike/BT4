### Title
Unprivileged `run_mainchain_gc` Permanently Destroys Verifiable Blocks, Causing Proof Lockout — (`contract/src/lib.rs`, `btc-types/src/contract_args.rs`)

---

### Summary

`run_mainchain_gc` carries no caller-identity access control — only a pause gate. Any NEAR account can invoke it at will. When called, it permanently deletes the oldest mainchain block from all three storage maps. Because `verify_transaction_inclusion` hard-panics when the target block is absent from those maps, an attacker can irrecoverably destroy the verifiability of any transaction whose block sits at the oldest retained position.

---

### Finding Description

**Access control on `run_mainchain_gc`**

The function is decorated with `#[pause(except(roles(Role::UnrestrictedRunGC)))]` only: [1](#0-0) 

This macro gates the call behind the contract's *pause* flag, but imposes no role check on who may call it when the contract is unpaused. Any NEAR account — including one with no staked tokens and no granted role — can submit a transaction calling `run_mainchain_gc(batch_size)`.

**What GC deletes**

For every height in `[start_removal_height, end_removal_height)`, GC calls `remove_block_header` and then removes the height entry: [2](#0-1) 

`remove_block_header` wipes the block from both `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

There is no mechanism to re-insert a pruned block. The deletion is permanent.

**How `verify_transaction_inclusion` fails**

The function first looks up the block in `mainchain_header_to_height`: [4](#0-3) 

If the block was GC'd, this `unwrap_or_else` hard-panics with `"block does not belong to the current main chain"`. The call never reaches the merkle-proof check.

**The off-by-one window that makes the attack reachable**

`verify_transaction_inclusion` enforces: [5](#0-4) 

This allows `confirmations == gc_threshold`. When the chain holds exactly `gc_threshold + 1` blocks (heights H … H+gc_threshold), a caller requesting `confirmations = gc_threshold` on block H passes both guards:

- `gc_threshold ≤ gc_threshold` ✓  
- `(H+gc_threshold) − H + 1 = gc_threshold + 1 ≥ gc_threshold` ✓

Yet GC will remove block H because `amount_of_headers_we_store (gc_threshold+1) > gc_threshold`: [6](#0-5) 

After GC runs, the same proof call panics unconditionally.

---

### Impact Explanation

Once a block is removed from `mainchain_header_to_height` and `headers_pool`, no on-chain path exists to restore it. Every transaction whose inclusion proof references that block hash is permanently unverifiable. For bridge or settlement contracts that rely on `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` as a gate, this constitutes an irreversible denial of fund release — permanent proof lockout — for all transactions in the pruned block.

---

### Likelihood Explanation

The attack requires no special role, no staked tokens, and no knowledge of pending proofs. An attacker needs only to:

1. Monitor the chain size (via the public `get_mainchain_size` view).
2. Call `run_mainchain_gc(N)` whenever the chain exceeds `gc_threshold`, targeting the oldest block.

Because NEAR processes transactions sequentially within a chunk, an attacker who submits `run_mainchain_gc` in the same chunk as (or any chunk before) a victim's `verify_transaction_inclusion` transaction will win. Even without timing precision, the attacker can pre-emptively prune blocks to prevent future proofs.

---

### Recommendation

Restrict `run_mainchain_gc` to a privileged role (e.g., `Role::DAO` or a dedicated `GCManager` role), mirroring the pattern used for `submit_blocks` which requires `#[trusted_relayer]`. The standalone public GC entry point should not be callable by arbitrary accounts. Alternatively, remove the public entry point entirely and rely solely on the internal call from `submit_blocks`.

---

### Proof of Concept

```rust
// State-machine test (pseudo-code, adaptable to near-workspaces)
// 1. Init contract with gc_threshold = T
// 2. Submit T+1 blocks so mainchain holds heights [H, H+T]
let oldest_hash = contract.view("get_block_hash_by_height", H).await;

// 3. Attacker (any unprivileged account) calls GC
attacker.call("run_mainchain_gc", { batch_size: 1 }).await;
// oldest_hash is now deleted from mainchain_header_to_height and headers_pool

// 4. Victim tries to verify a tx in the now-deleted block
let result = victim.call("verify_transaction_inclusion", ProofArgs {
    tx_block_blockhash: oldest_hash,
    confirmations: T,   // <= gc_threshold, passes first guard
    ...
}).await;

// result is Err: "block does not belong to the current main chain"
// The block was canonically valid at proof construction time.
// No recovery path exists.
assert!(result.is_err());
```

### Citations

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

**File:** contract/src/lib.rs (L401-409)
```rust
            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
