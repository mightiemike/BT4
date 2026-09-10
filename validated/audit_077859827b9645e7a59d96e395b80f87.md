### Title
`EthTransactionValidator::ForkTracker` fork-activation flags are only ever set to `true` and never cleared on reorg, causing pool admission to diverge from actual chain state - ([File: crates/transaction-pool/src/validate/eth.rs])

### Summary
`ForkTracker` in the transaction-pool validator caches whether Shanghai/Cancun/Prague/Osaka/Amsterdam are active as sticky `AtomicBool` flags. `EthTransactionValidator::on_new_head_block` (invoked from `Pool::on_canonical_state_change` on **every** canonical update, including `PoolUpdateKind::Reorg`) only ever calls `.store(true, ...)` when the new tip's timestamp satisfies a fork condition — it never calls `.store(false, ...)` when the new tip no longer satisfies it. On a deep reorg that moves the canonical tip to a block *before* a fork's activation timestamp, the tracker keeps reporting the fork as active even though the actual chain (as it will be validated by consensus) is pre-fork at that point. [1](#0-0) 

### Finding Description
`on_new_head_block` updates the fork flags unconditionally forward: [1](#0-0) 

Each `if chain_spec.is_X_active_at_timestamp(...) { fork_tracker.x.store(true, ...) }` branch has no corresponding `else { store(false, ...) }`. This is called for both `Commit` and `Reorg` events via `Pool::on_canonical_state_change`: [2](#0-1) [3](#0-2) 

The tracker's flags are then used by `validate_stateless`/`validate_stateful` to gate acceptance of fork-gated transaction types and intrinsic-gas/blob rules (e.g. EIP-7702 requires Prague, EIP-4844 requires Cancun): [4](#0-3) 

If the canonical chain reorgs backward across a fork boundary (e.g., the new canonical tip's timestamp is before the Cancun/Prague timestamp — plausible on a deep/long reorg, or a chain re-sync/drift scenario acknowledged elsewhere in `maintain.rs` via `MaintainedPoolState::Drifted`), `fork_tracker.cancun`/`prague`/etc. remain `true` from the prior (now-reverted) tip. The pool will continue to admit EIP-4844/EIP-7702 transactions as valid even though the actual current chain state (post-reorg) is before that fork's activation, and any block built by the node from these pool transactions would be rejected by `validate_block` / the execution-layer's own fork-gated tx-type checks, since consensus-side validation always re-derives fork activation from the header timestamp rather than from this stale in-memory flag.

This breaks the equality the codebase itself documents as required: pool admission must match block validity. It is directly analogous to the `supportsInterface` bug class — a state-tracking function that only updates one branch (the "activate" direction) of a bidirectional condition, silently omitting the opposite branch (the "deactivate" direction) that is required for correctness whenever the underlying condition can move backward (multiple-inheritance override chain vs. reorg/chain-state-reversal here).

### Impact Explanation
This matches the explicitly allowed impact category "pool admission diverging from block validity." Concretely:
- After a backward reorg past a fork boundary, the pool keeps accepting/holding transaction types (EIP-4844 blobs, EIP-7702 authorization lists) that are no longer valid for the new canonical tip.
- These transactions can be included by the node's own block builder into a payload built on top of the reorg'd (pre-fork) parent, producing a block that the node's own execution/validation logic (which derives fork activation from the header timestamp, not the stale tracker) — and every other client — would reject, i.e., a reth-built block gets rejected by consensus rules it should have known to enforce.
- This is a real behavioral divergence between the transaction pool's admission logic and the actual, spec-derived block-validity rules, matching "pool admission diverging from block validity" in the accepted impact list.

### Likelihood Explanation
Requires a chain reorg whose new canonical tip has a timestamp preceding a previously-activated timestamp-based hardfork. This is a possible (if uncommon) scenario in normal reorg handling, deep re-syncs, or when the node temporarily follows an alternate/attacker-influenced fork before reorging back — no malicious peer/attacker action is strictly required, only a legitimate reorg crossing a fork boundary, e.g. right at/near a fork's activation timestamp during network instability. `maintain.rs` itself already anticipates similar drift scenarios (`MaintainedPoolState::Drifted`), showing that non-canonical/backward-moving updates are a recognized real condition in this code path.

### Recommendation
In `EthTransactionValidator::on_new_head_block`, replace the one-directional `if active { store(true) }` pattern with an unconditional `store(chain_spec.is_X_active_at_timestamp(...), ...)` for each fork flag (shanghai/cancun/prague/osaka/amsterdam), so the tracker is always resynchronized to reflect the *current* tip rather than accumulating stale "activated" state across reorgs: [1](#0-0) 

### Proof of Concept
1. Configure a chain spec where Cancun activates at timestamp `T`.
2. Advance the canonical tip past `T` (e.g., via `on_canonical_state_change` with `PoolUpdateKind::Commit`); `fork_tracker.cancun` becomes `true`.
3. Trigger a `CanonStateNotification::Reorg` whose new tip has timestamp `< T` (reorging back before Cancun activation), calling `pool.on_canonical_state_change(..., PoolUpdateKind::Reorg)` → `validator.on_new_head_block(new_tip)`.
4. Because `chain_spec.is_cancun_active_at_timestamp(new_tip.timestamp())` is now `false`, the `if` branch in `on_new_head_block` is skipped and `fork_tracker.cancun` is never reset to `false`; `fork_tracker.is_cancun_activated()` still returns `true`.
5. Submit an EIP-4844 blob transaction to the pool; `validate_stateless` checks `self.fork_tracker.is_cancun_activated()` at [5](#0-4)  and incorrectly accepts it, even though the actual (reorg'd) chain tip is pre-Cancun and any block including this transaction would fail spec/consensus validation.

### Citations

**File:** crates/transaction-pool/src/validate/eth.rs (L595-613)
```rust
        if transaction.is_eip7702() {
            // Prague fork is required for 7702 txs
            if !self.fork_tracker.is_prague_activated() {
                return Err(InvalidTransactionError::TxTypeNotSupported.into())
            }

            if transaction.authorization_list().is_none_or(|l| l.is_empty()) {
                return Err(Eip7702PoolTransactionError::MissingEip7702AuthorizationList.into())
            }
        }

        ensure_intrinsic_gas(transaction, &self.fork_tracker)?;

        // light blob tx pre-checks
        if transaction.is_eip4844() {
            // Cancun fork is required for blob txs
            if !self.fork_tracker.is_cancun_activated() {
                return Err(InvalidTransactionError::TxTypeNotSupported.into())
            }
```

**File:** crates/transaction-pool/src/validate/eth.rs (L900-921)
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

**File:** crates/transaction-pool/src/pool/mod.rs (L532-540)
```rust
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
