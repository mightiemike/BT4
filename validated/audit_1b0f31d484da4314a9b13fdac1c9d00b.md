### Title
`ForkTracker` fork-activation flags are monotonic and never cleared on reorg, causing pool admission to diverge from actual chain state - (File: `crates/transaction-pool/src/validate/eth.rs`)

### Summary
`EthTransactionValidator::on_new_head_block` updates a set of `AtomicBool` fork flags (`fork_tracker.shanghai/cancun/prague/osaka/amsterdam`, and internally `is_osaka_activated()`) every time a new canonical tip is processed. The flags are only ever written with `store(true, ...)` and are never reset to `false`, even when the canonical chain reorgs backward past the block/timestamp that originally activated a fork. This mirrors the Sophon `setStartBlock()` bug class: a global "point in time" parameter is updated in one place (the head tracker), but pre-existing/derived per-fork state is not resynchronized to match, so validation keeps using stale fork-activation data instead of the state implied by the new canonical head.

### Finding Description
`on_new_head_block` is invoked from `EthTransactionValidator` on every canonical state update (see `crates/transaction-pool/src/pool/mod.rs::on_canonical_state_change`, which calls `self.validator.on_new_head_block(new_tip)` for both `Commit` and `Reorg` update kinds via `maintain.rs`). [1](#0-0) [2](#0-1) 

Inside `on_new_head_block`, each fork flag is updated with a one-directional check-and-store:
```rust
if self.chain_spec().is_osaka_active_at_timestamp(new_tip_block.timestamp()) {
    self.fork_tracker.osaka.store(true, std::sync::atomic::Ordering::Relaxed);
}
``` [3](#0-2) 

There is no corresponding `else { store(false, ...) }` branch, and no other code path resets these flags. `maintain_transaction_pool` handles `CanonStateNotification::Reorg` by calling `pool.on_canonical_state_change(update)` with the new (reorged) tip, which internally calls `validator.on_new_head_block(new_tip)` — but because the flags are monotonic, if the reorg moves the timestamp/height backward across a fork boundary (e.g., from post-Osaka back to pre-Osaka, which can legitimately happen on devnets/testnets or short-lived reorgs around a fork boundary), `fork_tracker.osaka` (and the other flags) remain `true`. [4](#0-3) [5](#0-4) 

This stale flag is then read in transaction validation logic, e.g. `validate_eip4844`, which branches on `self.fork_tracker.is_osaka_activated()` to decide whether EIP-4844 (v0) vs EIP-7594 (v1) blob sidecars are acceptable:
```rust
if self.eip7594 {
    if self.fork_tracker.is_osaka_activated() {
        if sidecar.is_eip4844() {
            return Err(... UnexpectedEip4844SidecarAfterOsaka)
        }
    } else if sidecar.is_eip7594() && !self.allow_7594_sidecars() {
        return Err(... UnexpectedEip7594SidecarBeforeOsaka)
    }
}
``` [6](#0-5) 

After a backward reorg past the Osaka boundary, the pool would incorrectly keep enforcing post-Osaka sidecar rules (rejecting legitimate pre-Osaka EIP-4844 v0 sidecars, or wrongly permitting v1/EIP-7594 sidecars) even though the actual canonical chain state, per `chain_spec.is_osaka_active_at_timestamp`, is now pre-fork. This is exactly the `setStartBlock()`-style bug: the authoritative "current time/config" (canonical tip) changes, but a cached derived flag tied to it is not resynchronized, so subsequent logic keeps operating under the old assumption.

### Impact Explanation
This causes transaction-pool admission to diverge from actual block validity rules derived from `EthereumHardforks`/`ChainSpec` for the reorged chain: transactions that are valid under the current (reorged) fork rules can be wrongly rejected, and/or transactions that should be rejected under the current fork rules can be wrongly admitted into the pool and potentially included by a builder into a block that other clients (validating against the correct, non-stale fork state) would reject. This matches the "pool admission diverging from block validity" impact category (High), since it can either stall legitimate transaction propagation or let a builder construct an invalid block that gets rejected downstream.

### Likelihood Explanation
Requires a reorg that crosses a fork activation boundary (timestamp-based hardfork such as Osaka/Cancun/Prague/Shanghai/Amsterdam). On mainnet this is extremely unlikely once a fork is finalized, but it is realistic on devnets, testnets, or in the window immediately around a fork's activation where short reorgs across the boundary are plausible. The flags being strictly monotonic (`store(true, ...)` only) is a structural defect regardless of how often the triggering reorg occurs.

### Recommendation
In `on_new_head_block`, replace the one-directional `if active { store(true) }` checks with two-directional updates (`store(is_active, Ordering::Relaxed)`) for every fork flag in `ForkTracker`, so the flags always reflect the fork state implied by the current canonical tip, including on backward reorgs.

### Proof of Concept
1. Start a node with Osaka scheduled at some timestamp `T`.
2. Advance the canonical head past `T` so `on_new_head_block` sets `fork_tracker.osaka = true`.
3. Trigger a reorg (`CanonStateNotification::Reorg`) to a new tip with timestamp `< T` (still valid per consensus rules, e.g. a short reorg around the fork boundary).
4. Submit an EIP-4844 (v0/legacy) blob transaction with a valid pre-Osaka sidecar.
5. Observe `validate_eip4844` still evaluates `self.fork_tracker.is_osaka_activated() == true`, incorrectly rejecting the sidecar with `UnexpectedEip4844SidecarAfterOsaka` even though the current canonical tip is pre-Osaka according to `chain_spec.is_osaka_active_at_timestamp`.

### Citations

**File:** crates/transaction-pool/src/validate/eth.rs (L825-846)
```rust
                    // EIP-7594 sidecar version handling
                    if self.eip7594 {
                        // Standard Ethereum behavior
                        if self.fork_tracker.is_osaka_activated() {
                            if sidecar.is_eip4844() {
                                return Err(InvalidPoolTransactionError::Eip4844(
                                    Eip4844PoolTransactionError::UnexpectedEip4844SidecarAfterOsaka,
                                ))
                            }
                        } else if sidecar.is_eip7594() && !self.allow_7594_sidecars() {
                            return Err(InvalidPoolTransactionError::Eip4844(
                                Eip4844PoolTransactionError::UnexpectedEip7594SidecarBeforeOsaka,
                            ))
                        }
                    } else {
                        // EIP-7594 disabled: always reject v1 sidecars, accept v0
                        if sidecar.is_eip7594() {
                            return Err(InvalidPoolTransactionError::Eip4844(
                                Eip4844PoolTransactionError::Eip7594SidecarDisallowed,
                            ))
                        }
                    }
```

**File:** crates/transaction-pool/src/validate/eth.rs (L900-920)
```rust
    fn on_new_head_block(&self, new_tip_block: &HeaderTy<Evm::Primitives>) {
        // update all forks
        if self.chain_spec().is_shanghai_active_at_timestamp(new_tip_block.timestamp()) {
            self.fork_tracker.shanghai.store(true, std::sync::atomic::Ordering::Relaxed);
        }

        if self.chain_spec().is_cancun_active_at_timestamp(new_tip_block.timestamp()) {
            self.fork_tracker.cancun.store(true, std::sync::atomic::Ordering::Relaxed);
        }

        if self.chain_spec().is_prague_active_at_timestamp(new_tip_block.timestamp()) {
            self.fork_tracker.prague.store(true, std::sync::atomic::Ordering::Relaxed);
        }

        if self.chain_spec().is_osaka_active_at_timestamp(new_tip_block.timestamp()) {
            self.fork_tracker.osaka.store(true, std::sync::atomic::Ordering::Relaxed);
        }

        if self.chain_spec().is_amsterdam_active_at_timestamp(new_tip_block.timestamp()) {
            self.fork_tracker.amsterdam.store(true, std::sync::atomic::Ordering::Relaxed);
        }
```

**File:** crates/transaction-pool/src/pool/mod.rs (L531-540)
```rust
    /// Updates the entire pool after a new block was executed.
    pub fn on_canonical_state_change(&self, update: CanonicalStateUpdate<'_, V::Block>) {
        trace!(target: "txpool", ?update, "updating pool on canonical state change");

        let block_info = update.block_info();
        let CanonicalStateUpdate {
            new_tip, changed_accounts, mined_transactions, update_kind, ..
        } = update;
        self.validator.on_new_head_block(new_tip);

```

**File:** crates/transaction-pool/src/maintain.rs (L321-344)
```rust
            CanonStateNotification::Reorg { old, new } => {
                let (old_blocks, old_state) = old.inner();
                let (new_blocks, new_state) = new.inner();
                let new_tip = new_blocks.tip();
                let new_first = new_blocks.first();
                let old_first = old_blocks.first();

                // check if the reorg is not canonical with the pool's block
                if !(old_first.parent_hash() == pool_info.last_seen_block_hash ||
                    new_first.parent_hash() == pool_info.last_seen_block_hash)
                {
                    // the new block points to a higher block than the oldest block in the old chain
                    maintained_state = MaintainedPoolState::Drifted;
                }

                let chain_spec = client.chain_spec();

                // fees for the next block: `new_tip+1`
                let pending_block_base_fee = chain_spec
                    .next_block_base_fee(new_tip.header(), new_tip.timestamp())
                    .unwrap_or_default();
                let pending_block_blob_fee = new_tip.header().maybe_next_block_blob_fee(
                    chain_spec.blob_params_at_timestamp(new_tip.timestamp()),
                );
```

**File:** crates/transaction-pool/src/maintain.rs (L415-425)
```rust
                // update the pool first
                let update = CanonicalStateUpdate {
                    new_tip: new_tip.sealed_block(),
                    pending_block_base_fee,
                    pending_block_blob_fee,
                    changed_accounts,
                    // all transactions mined in the new chain need to be removed from the pool
                    mined_transactions,
                    update_kind: PoolUpdateKind::Reorg,
                };
                pool.on_canonical_state_change(update);
```
