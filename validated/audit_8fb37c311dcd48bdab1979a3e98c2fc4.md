This confirms `l1_fee_rate` is a value passed via `CitreaChain::new(l1_fee_rate)` which is derived from `context.l1_fee_rate()`, itself set from the committed `l2_block.l1_fee_rate()` field via `HookL2BlockInfo::new` — not from any live fee oracle read during replay. The mempool-side dynamic `simulate_tx_l1_fee` (used only for RPC admission/estimation) explicitly runs with a hypothetical rate and is disconnected from block execution/replay.

### Title
No vulnerability: `l1_fee_rate` and `timestamp` are fully committed in the signed `L2Header` and execution is deterministic - (File: crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs)

### Summary
The premise that EVM execution could diverge between an honest full node and the batch prover due to "fee-rate/timestamp order-dependence" does not hold. Both `l1_fee_rate` and `timestamp` are fields of the sequencer-signed `L2Header` (hashed into `L2Header::compute_digest`), not values read from any live oracle or node-local mempool state during replay. Since both the honest full node's `apply_l2_block` (`crates/common/src/l2.rs:111-125`) and the batch prover's guest-side `apply_l2_blocks_from_sequencer_commitments` invoke the identical `StfBlueprint::apply_l2_block` with the identical `L2Block` (identical `txs`, `l1_fee_rate`, `timestamp`), execution is fully deterministic and cannot diverge based on "timing."

### Finding Description
The claimed binding is: `final_root` computed by `StfBlueprint::apply_l2_block` for a given `L2Block` == `state_root` field signed in that block's header. Tracing the code:

- `L2Header` (`crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs:14-27`) contains `l1_fee_rate` and `timestamp` as committed fields, both included in `compute_digest` (lines 70-79), which is what the sequencer signs (`SignedL2Header`).
- `HookL2BlockInfo::new` (`crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs:128-143`) derives `l1_fee_rate: l2_block.l1_fee_rate()` and `timestamp: l2_block.timestamp()` directly from the signed header — not from any external oracle.
- `Evm::begin_l2_block_hook` (`crates/evm/src/hooks.rs:84-93`) sets `BlockEnv.timestamp = l2_block_info.timestamp()`, taken from the committed value.
- `Evm::call` (`crates/evm/src/call.rs:54-55`) sets `l1_fee_rate = context.l1_fee_rate()`, feeding `CitreaChain::new(l1_fee_rate)` (`crates/evm/src/evm/handler.rs:130-142`), which is consumed deterministically in `output()` (`crates/evm/src/evm/handler.rs:578-593`) to charge the L1 fee — again sourced from the committed header field, not a live fee oracle.
- The only place a "live" fee-rate estimate appears is `Evm::simulate_tx_l1_fee` (`crates/evm/src/query.rs:2309-2370`) and `CitreaTransactionValidator::validate_transaction` (`crates/sequencer/src/tx_validator.rs:49-116`) — these are used solely for **mempool admission/estimation** on the sequencer side, never for block execution or replay/proving. They cannot affect the deterministic outcome of `apply_l2_block` on committed `l2_block.txs`.

Since both the full node and the batch-prover circuit execute the exact same `L2Block` (same `txs`, same committed `l1_fee_rate`, same committed `timestamp`) through the same `StfBlueprint::apply_l2_block` function against the same pre-state, there is no code path by which "gas-price-dependent EVM config" or "mempool ordering" could cause the two independent executions to diverge — mempool ordering only affects block *construction* by the sequencer, not block *replay/verification*, which operates purely on the already-ordered `l2_block.txs` list.

### Impact Explanation
No impact: no divergence is demonstrated or possible via any documented code path, since the relevant inputs (`l1_fee_rate`, `timestamp`, `txs`) are all fully committed and signed in `L2Header`, and execution against them is a pure deterministic function of those inputs plus prior state.

### Likelihood Explanation
Not applicable — the question's premise (existence of an unfixed, uncommitted, oracle-dependent or ordering-dependent EVM input) is false for this codebase.

### Recommendation
No action needed; existing design (embedding `l1_fee_rate` and `timestamp` in the signed `L2Header`, and executing fixed `txs` lists deterministically) already prevents this class of divergence.

### Proof of Concept
Not applicable — no divergence to reproduce. Existing tests `test_panic_state_root_assertion_failure` and `test_panic_state_root_mismatch_assertion` (`crates/citrea-stf/tests/blueprint.rs:1164-1256`, `1418-1510`) already demonstrate the guest correctly panics on any state-root mismatch, confirming the assertion functions as intended rather than being bypassable via fee-rate/timestamp manipulation. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs (L14-27)
```rust
pub struct L2Header {
    /// The block height/number in the L2 chain
    height: u64,
    /// Hash of the previous block in the chain
    prev_hash: [u8; 32],
    /// Merkle root of the state tree after applying this block's transactions
    state_root: [u8; 32],
    /// Fee rate for L1 transactions associated with this block
    l1_fee_rate: u128,
    /// Merkle root of all transactions included in this block
    tx_merkle_root: [u8; 32],
    /// Unix timestamp when this block was created
    timestamp: u64,
}
```

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs (L63-79)
```rust
    /// Computes the cryptographic digest of the block header using the specified hash function.
    ///
    /// # Type Parameters
    /// * `D` - The type of hash function to use (must implement the `Digest` trait)
    ///
    /// # Returns
    /// The computed hash digest of the header
    pub fn compute_digest(&self) -> [u8; 32] {
        let mut hasher = sha2::Sha256::new();
        hasher.update(self.height.to_be_bytes());
        hasher.update(self.prev_hash);
        hasher.update(self.state_root);
        hasher.update(self.l1_fee_rate.to_be_bytes());
        hasher.update(self.tx_merkle_root);
        hasher.update(self.timestamp.to_be_bytes());
        <[u8; 32]>::from(hasher.finalize_fixed())
    }
```

**File:** crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs (L128-143)
```rust
impl HookL2BlockInfo {
    pub fn new(
        l2_block: &L2Block,
        pre_state_root: StorageRootHash,
        current_spec: SpecId,
        sequencer_pub_key: K256PublicKey,
    ) -> Self {
        Self {
            l2_height: l2_block.height(),
            pre_state_root,
            current_spec,
            sequencer_pub_key,
            l1_fee_rate: l2_block.l1_fee_rate(),
            timestamp: l2_block.timestamp(),
        }
    }
```

**File:** crates/evm/src/hooks.rs (L84-93)
```rust
        let new_pending_env = BlockEnv {
            number: parent_block_number + 1,
            beneficiary: cfg.coinbase,
            timestamp: l2_block_info.timestamp(),
            prevrandao: Some(B256::ZERO),
            basefee,
            gas_limit: cfg.block_gas_limit,
            difficulty: U256::ZERO,
            blob_excess_gas_and_price,
        };
```

**File:** crates/evm/src/call.rs (L50-56)
```rust
        let cfg = self.cfg.get(working_set).expect("Evm config must be set");
        let active_evm_spec = citrea_spec_id_to_evm_spec_id(context.active_spec());
        let cfg_env = get_cfg_env(cfg, active_evm_spec);

        let l1_fee_rate = context.l1_fee_rate();
        let mut citrea_handler_ext = CitreaChain::new(l1_fee_rate);

```

**File:** crates/evm/src/evm/handler.rs (L136-148)
```rust
impl CitreaChain {
    pub(crate) fn new(l1_fee_rate: u128) -> Self {
        Self {
            l1_fee_rate,
            ..Default::default()
        }
    }
}

impl CitreaChainExt for CitreaChain {
    fn l1_fee_rate(&self) -> u128 {
        self.l1_fee_rate
    }
```

**File:** crates/evm/src/evm/handler.rs (L566-593)
```rust
    #[cfg_attr(feature = "native", instrument(level = "trace", skip_all, fields(caller = %evm.ctx_ref().tx().caller())))]
    fn output(
        &self,
        evm: &mut Self::Evm,
        result: <Self::Frame as Frame>::FrameResult,
    ) -> Result<revm::context::result::ResultAndState<Self::HaltReason>, Self::Error> {
        let uncompressed_size = calc_diff_size(evm.ctx());

        // Estimate the size of the state diff after the brotli compression and add L1 fee overhead
        let diff_size = (uncompressed_size * BROTLI_COMPRESSION_PERCENTAGE / 100) as u64
            + L1_FEE_OVERHEAD as u64;

        let l1_fee_rate = evm.ctx().chain().l1_fee_rate();
        let l1_fee = U256::from(l1_fee_rate) * U256::from(diff_size);
        evm.ctx().chain().set_tx_info(TxInfo {
            l1_diff_size: diff_size,
            l1_fee,
        });
        // System caller doesn't pay L1 fee.
        if !evm.is_system_caller() {
            if let Some(_out_of_funds) = decrease_caller_balance(evm.ctx(), l1_fee)? {
                return Err(ERROR::from_string(format!(
                    "Not enough funds for L1 fee: {l1_fee}"
                )));
            }
            // add l1 fee to l1 fee vault
            change_balance(evm.ctx(), l1_fee, true, L1_FEE_VAULT)?;
        }
```

**File:** crates/evm/src/query.rs (L2309-2361)
```rust
    pub fn simulate_tx_l1_fee(
        &self,
        tx: &Recovered<TransactionSigned>,
        working_set: &mut WorkingSet<C::Storage>,
        fork_fn: impl Fn(u64) -> Fork,
    ) -> Result<EstimatedTxExpenses, EthApiError> {
        // Rate the block builder will price the next block with (last sealed block's rate).
        // Guard explicitly instead of relying on the `.expect()` inside `get_pending_block_env`,
        // as a panic here would take down the mempool validation task.
        let l1_fee_rate = self
            .blocks
            .last(&mut working_set.accessory_state())
            .ok_or(EthApiError::HeaderNotFound(BlockNumberOrTag::Latest.into()))?
            .l1_fee_rate;

        let block_env = get_pending_block_env(self, working_set);
        let citrea_spec_id = fork_fn(block_env.number).spec_id;
        let evm_spec_id = citrea_spec_id_to_evm_spec_id(citrea_spec_id);

        let cfg = self
            .cfg
            .get(working_set)
            .expect("EVM chain config should be set");
        let mut cfg_env = get_cfg_env(cfg, evm_spec_id);
        // The pool legitimately holds transactions that are not yet includable: below the
        // current base fee (base-fee subpool) or with a future nonce (queued subpool). We only
        // want the diff size, so relax these checks. Balance checks stay ON so the L2 spend
        // semantics remain real (reth already guaranteed `balance >= cost()`).
        cfg_env.disable_base_fee = true;
        cfg_env.disable_nonce_check = true;
        cfg_env.disable_eip3607 = true;

        let mut tx_env = create_tx_env(tx);
        // Never let the simulation claim more gas than a block can hold.
        tx_env.gas_limit = tx_env.gas_limit.min(block_env.gas_limit);

        // Capture block-derived fields before `block_env` is moved into the executor.
        let base_fee = U256::from(block_env.basefee);
        let block_gas_limit = U64::from(block_env.gas_limit);

        let evm_db = self.get_db(working_set, citrea_spec_id);
        let (result_and_state, tx_info) = inspect_with_citrea_handler(
            evm_db,
            cfg_env,
            block_env,
            tx_env,
            0, // l1_fee_rate = 0; real fee recovered below
            TracingInspector::new(TracingInspectorConfig::none()),
        )
        .map_err(EthApiError::from)?;

        let l1_diff_size = tx_info.l1_diff_size;
        let l1_fee = U256::from(l1_fee_rate).saturating_mul(U256::from(l1_diff_size));
```

**File:** crates/sequencer/src/tx_validator.rs (L49-82)
```rust
    async fn validate_transaction(
        &self,
        origin: TransactionOrigin,
        transaction: Self::Transaction,
    ) -> TransactionValidationOutcome<Self::Transaction> {
        // Stock validation first. This is the only `.await`; everything below is synchronous
        // CPU work, so no non-`Send` revm value ever crosses an await point.
        let outcome = self.inner.validate_transaction(origin, transaction).await;

        // Only transactions reth deems valid proceed to the L1-fee reservation. Invalid/Error
        // outcomes pass straight through.
        let TransactionValidationOutcome::Valid {
            balance,
            state_nonce,
            transaction,
            propagate,
        } = outcome
        else {
            return outcome;
        };

        // reth's reserved L2 cost already includes `value` (`max_fee_per_gas*gas_limit + value`),
        // and reth has guaranteed `balance >= cost()`. Simulate to price the extra L1 fee.
        // Borrows of `transaction` are confined to this block so it can be moved afterwards.
        let reth_cost = *transaction.transaction().cost();
        let sim = {
            let recovered = transaction.transaction().transaction();
            let mut working_set = WorkingSet::new(self.provider.storage.clone());
            self.provider.evm.simulate_tx_l1_fee(
                recovered,
                &mut working_set,
                fork_from_block_number,
            )
        };
```

**File:** crates/common/src/l2.rs (L104-125)
```rust
    let l2_block_result = {
        // Post tangerine, we do not have the slot hash in l2 blocks we inspect the txs and get the slot hashes from set block infos
        // Then store the short header proofs of those blocks in the ledger db

        decode_sov_tx_and_update_short_header_proofs(l2_block_response, ledger_db, da_service)
            .await?;

        execute_l2_block::<Da>(
            stf,
            &l2_block,
            pre_state,
            current_spec,
            &current_state_root,
            sequencer_pub_key,
        )?
    };

    let next_state_root = l2_block_result.state_root_transition.final_root;
    // Check if post state root is the same as the one in the l2 block
    if next_state_root.as_ref().to_vec() != l2_block.state_root() {
        bail!("Post state root mismatch at height: {l2_height}")
    }
```

**File:** crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs (L641-673)
```rust
                if let Some(prev_hash) = prev_l2_block_hash {
                    assert_eq!(
                        l2_block.prev_hash(),
                        prev_hash,
                        "L2 block previous hash must match the hash of the block before"
                    );
                }

                fork_manager.register_block(l2_height).unwrap();

                let result = self
                    .apply_l2_block(
                        fork_manager.active_fork().spec_id,
                        &sequencer_public_key,
                        &current_state_root,
                        pre_state.clone(),
                        cumulative_state_log,
                        cumulative_offchain_log,
                        state_witness,
                        offchain_witness,
                        &l2_block,
                    )
                    // TODO: this can be just ignoring the failing seq. com.
                    // We can count a failed l2 block as a valid state transition.
                    // for now we don't allow "broken" seq. com.s
                    .expect("L2 block must succeed");

                assert_eq!(current_state_root, result.state_root_transition.init_root);
                current_state_root = result.state_root_transition.final_root;
                state_diff.extend(result.state_diff);

                // The state root of prover should match l2 block coming from sequencer
                assert_eq!(current_state_root, l2_block.state_root());
```
