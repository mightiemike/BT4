This confirms the analog: `on_new_head_block` in `crates/transaction-pool/src/validate/eth.rs` is invoked on **every** canonical state update — including `CanonStateNotification::Reorg` events via `on_canonical_state_change` in `crates/transaction-pool/src/pool/mod.rs` and `maintain_transaction_pool` in `crates/transaction-pool/src/maintain.rs`. This is exactly the Wildcat "monotonic registration" pattern applied to fork activation flags.

### Title
`ForkTracker` fork-activation flags are monotonic and never cleared on reorg, causing pool admission to diverge from block validity - (File: crates/transaction-pool/src/validate/eth.rs)

### Summary
`EthTransactionValidator::on_new_head_block` only ever *sets* fork-activation `AtomicBool`s (`shanghai`, `cancun`, `prague`, `osaka`, `amsterdam`) to `true` when the new tip's timestamp indicates the fork is active; there is no corresponding branch that sets them back to `false` when a new tip's timestamp indicates the fork is *not yet* active [1](#0-0) . This function is called on every `CanonStateNotification`, including `Reorg` events, via `Pool::on_canonical_state_change` [2](#0-1)  which is driven by `maintain_transaction_pool`'s reorg handling [3](#0-2) .

### Finding Description
The `ForkTracker` fields are read via `is_prague_activated()`, `is_cancun_activated()`, etc., and gate stateless validation of fork-gated transaction types (EIP-4844 blob txs require Cancun, EIP-7702 set-code txs require Prague) [4](#0-3) , sender bytecode/delegation checks [5](#0-4) , and intrinsic gas `SpecId` selection [6](#0-5) .

Because `on_new_head_block` never resets these flags to `false`, once a tip whose timestamp satisfies `is_prague_active_at_timestamp` (etc.) has been observed, `fork_tracker.prague` remains `true` forever, even if a subsequent reorg replaces the canonical tip with a chain whose new tip timestamp is earlier and no longer satisfies the fork condition. This breaks the invariant that pool-level fork gating must match `ChainSpec` evaluation against the *current* canonical tip — the equality `fork_tracker.is_prague_activated() == chain_spec.is_prague_active_at_timestamp(current_tip.timestamp())` can be violated after a reorg to an earlier-timestamped branch.

### Impact Explanation
If `fork_tracker.prague` is stuck `true` after a reorg to a pre-Prague tip, the pool will accept EIP-7702 transactions and 7702-delegated sender bytecode as valid and admit them into the pool, even though a block builder operating against the actual (post-reorg) chain state/spec would reject a block containing such a transaction, since execution against the correct `SpecId` would not recognize EIP-7702 semantics. This is exactly "pool admission diverging from block validity" — a transaction the pool holds as valid can never be validly included in a block built on the correct fork, silently occupying pool slots and potentially being repeatedly gossiped/rejected by peers on relay, or causing a builder using this pool to attempt an invalid block. The same monotonic-flag issue applies to Cancun blob-tx gating, Shanghai init-code-size gating, and Amsterdam gas accounting (`is_amsterdam_eip8037_enabled`), broadening the blast radius of any deep/multi-fork reorg.

### Likelihood Explanation
This requires a reorg where the new canonical tip's timestamp evaluates to "fork not yet active" after a previous tip evaluated "fork active" — i.e., a reorg across a fork-activation timestamp boundary, or more generally any multi-slot reorg that moves the tip's timestamp backward across an activation threshold. Under Ethereum's PoS single-slot/short reorgs this is a narrow window (typically only occurs near the activation boundary itself, or in networks with irregular/testing slot production), so likelihood is low but not zero, particularly on non-mainnet/dev/testnets performing hardfork activation testing where deep reorgs across the activation boundary are more likely to be exercised.

### Recommendation
In `on_new_head_block`, explicitly set each fork flag based on the new tip's evaluation rather than only setting `true`:
```rust
self.fork_tracker.prague.store(
    self.chain_spec().is_prague_active_at_timestamp(new_tip_block.timestamp()),
    std::sync::atomic::Ordering::Relaxed,
);
```
for all fork flags (`shanghai`, `cancun`, `prague`, `osaka`, `amsterdam`), removing the one-directional `if ... { store(true, ...) }` pattern so the tracker always reflects the fork status of the current canonical tip, matching behavior symmetrically for both forward progression and reorgs.

### Proof of Concept
1. Configure a chain spec where Prague activates at timestamp `T`.
2. Advance the canonical tip to a block with timestamp `>= T` via `Commit`; `EthTransactionValidator::on_new_head_block` sets `fork_tracker.prague = true` [7](#0-6) .
3. Submit an EIP-7702 transaction; it is accepted into the pool because `fork_tracker.is_prague_activated()` returns `true` [8](#0-7) .
4. Trigger a `CanonStateNotification::Reorg` to a competing branch whose new tip has timestamp `< T` (e.g. a multi-slot reorg back past the activation boundary) — this flows through `maintain_transaction_pool`'s reorg arm into `pool.on_canonical_state_change` [9](#0-8)  and thus `validator.on_new_head_block(new_tip)` [10](#0-9)  with the earlier timestamp.
5. Because the `if` condition in `on_new_head_block` is false for this earlier timestamp, `fork_tracker.prague` is never reset and remains `true`.
6. Submit another EIP-7702 transaction (or a 7702-delegated sender) after the reorg; it is still accepted by the pool even though the canonical chain has reorged to a pre-Prague state, demonstrating pool admission diverging from actual block-validity rules for the current chain.

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

**File:** crates/transaction-pool/src/validate/eth.rs (L734-749)
```rust
        if let Some(code_hash) = &sender.bytecode_hash &&
            *code_hash != KECCAK_EMPTY
        {
            let is_eip7702 = if self.fork_tracker.is_prague_activated() {
                match state.bytecode_by_hash(code_hash) {
                    Ok(bytecode) => bytecode.unwrap_or_default().is_eip7702(),
                    Err(err) => {
                        return Err(TransactionValidationOutcome::Error(
                            *transaction.hash(),
                            Box::new(err),
                        ))
                    }
                }
            } else {
                false
            };
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

**File:** crates/transaction-pool/src/validate/eth.rs (L1503-1511)
```rust
    let spec_id = if fork_tracker.is_amsterdam_activated() {
        SpecId::AMSTERDAM
    } else if fork_tracker.is_prague_activated() {
        SpecId::PRAGUE
    } else if fork_tracker.is_shanghai_activated() {
        SpecId::SHANGHAI
    } else {
        SpecId::MERGE
    };
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

**File:** crates/transaction-pool/src/maintain.rs (L416-425)
```rust
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
