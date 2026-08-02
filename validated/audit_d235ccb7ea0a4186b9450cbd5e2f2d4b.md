## Analysis

The external report's core invariant is: **an integrity/consistency check must not silently omit a component of committed state, or that omission can mask genuine divergence.** In `AlgebraPool`, that took the form of a missing failure path; in Aptos-core, I found a structurally similar gap in the code that verifies committed state (`ensure_match_transaction_info`), used specifically by replay/verify tooling.

### Title
Replay-verification comparator (`ensure_match_transaction_info`) ignores state-checkpoint hashes, allowing `replay-verify`/`db-tool replay-on-archive` to report success despite a diverged committed state root — (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function that `db-tool`'s `replay_on_archive` (and `aptos-debugger`) use to assert that locally re-executed transaction outputs match the authenticated `TransactionInfo` pulled from a backup/archive. It checks status, gas, write-set hash, and event-root hash, but explicitly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — fields that are part of the authenticated `TransactionInfo`/`TransactionInfoV1` and are bound into the transaction accumulator root that ledger infos commit to.

### Finding Description
`ensure_match_transaction_info` at [1](#0-0)  validates status, gas used, write-set hash (`state_change_hash`) and event-root hash against the supplied `TransactionInfo`, but the function's own TODO comment documents that the checkpoint hashes are intentionally skipped: [2](#0-1) 

These checkpoint hashes are real fields of the on-chain-committed `TransactionInfo`/`TransactionInfoV1` structure (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`), which are folded into the accumulator leaf hash via `CryptoHash` on `TransactionInfo` [3](#0-2) , and thus into the transaction-accumulator root that `LedgerInfo` commits to and that clients verify proofs against (`TransactionInfoWithProof::verify`, `TransactionAccumulatorProof::verify`) [4](#0-3) [5](#0-4) .

`ensure_match_transaction_info` is the exact assertion used by `storage/db-tool/src/replay_on_archive.rs::execute_and_verify` to accept or reject a replayed chunk as matching the archived, authenticated ledger data: [6](#0-5) . This tool is a mainnet-facing integrity/replay-verification pipeline: it re-executes archived transactions and is meant to detect any divergence between local VM execution and the authenticated, committed ledger. Because the comparator omits the checkpoint hash fields, a replay whose write set, events, gas, and status happen to match, but whose underlying state-checkpoint/hot-state/position-state root diverges from the authenticated one, will be reported as a **successful, verified replay** even though the state trees it derives from do not match what was actually committed and proof-bound on mainnet.

The feature this most directly threatens is `compute_trading_native_state_roots`, which is exactly the on-chain-config-gated code path (`BlockExecutorConfigFromOnchain::compute_trading_native_state_roots`, threaded through `DoGetExecutionOutput` and `DoLedgerUpdate`) that produces the `position_state_checkpoint_hash` this comparator ignores [7](#0-6) [8](#0-7) . The comment itself flags that enabling this feature is unsafe until the gap is closed.

### Impact Explanation
This is a state/proof-integrity gap in the authenticated verification path rather than in consensus execution itself: `replay_on_archive` is used to certify that historical execution/state-commitment on mainnet is reproducible and correct (a hard-fork/divergence detector). A silent omission here means a genuine committed-state divergence — e.g., from a bug in the position/hot-state checkpoint computation, a JMT/restore inconsistency, or a malicious/corrupted archive with correct write-set/events but wrong checkpoint hash — would not be caught by this tool, undermining its entire purpose of catching hard-fork-only divergence during replay/restore. This matches the in-scope category "Hard-fork-only divergence during commit, replay, restore, or proof verification," since the broken invariant is precisely that authenticated proof-bearing fields (checkpoint hashes bound into the accumulator) are not checked, so a wrong root could be accepted as valid by the tool relying on this function.

### Likelihood Explanation
The likelihood of this specific comparator gap firing under real conditions is currently limited by the fact that `position_state_checkpoint_hash`/`compute_trading_native_state_roots` appears to be a not-yet-fully-enabled feature (guarded by an on-chain config and explicitly flagged unsafe by the author's own TODO). However, the code path is already wired end-to-end (executor → ledger update → transaction info → replay verifier), so as soon as `compute_trading_native_state_roots` (or `hot_state_checkpoint_hash` generally) is turned on, this is a straightforward, deterministic gap — no adversarial timing or race condition is required, only a divergence in checkpoint-hash computation somewhere in the state-checkpoint pipeline.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` (when present in the expected `TransactionInfo`) against values recomputed locally, and fail loudly if they diverge, before `compute_trading_native_state_roots` (or hot-state checkpointing generally) is enabled in production replay/verify tooling.

### Proof of Concept
1. Enable (or simulate enabling) `compute_trading_native_state_roots` / hot-state checkpointing so that `TransactionInfoV1` carries non-`None` `position_state_checkpoint_hash`/`hot_state_checkpoint_hash`.
2. Introduce (or naturally trigger via a genuine bug) a divergence solely in the position/hot-state checkpoint computation while keeping write set, events, gas, and status identical to the archived `TransactionInfo`.
3. Run `db-tool replay-on-archive` over the affected version range; `execute_and_verify` calls `ensure_match_transaction_info` [9](#0-8) , which will return `Ok(())` because it never inspects the checkpoint hash fields, despite the underlying committed state root differing from the authenticated one.

**Note:** I was not able to fully verify within this session whether `compute_trading_native_state_roots`/hot-state checkpointing is currently enabled on mainnet vs. still gated behind a disabled on-chain feature flag or config default — this affects the current real-world likelihood and should be confirmed by a deeper review of `types/src/on_chain_config/aptos_features.rs` and the relevant `BlockExecutorConfigFromOnchain` defaults.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2204)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
        const ERR_MSG: &str = "TransactionOutput does not match TransactionInfo";

        let expected_txn_status: TransactionStatus = txn_info.status().clone().into();
        ensure!(
            self.status() == &expected_txn_status,
            "{}: version:{}, status:{:?}, auxiliary data:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.status(),
            self.auxiliary_data(),
            expected_txn_status,
        );

        ensure!(
            self.gas_used() == txn_info.gas_used(),
            "{}: version:{}, gas_used:{:?}, expected:{:?}",
            ERR_MSG,
            version,
            self.gas_used(),
            txn_info.gas_used(),
        );

        let write_set_hash = CryptoHash::hash(self.write_set());
        ensure!(
            write_set_hash == txn_info.state_change_hash(),
            "{}: version:{}, write_set_hash:{:?}, expected:{:?}, write_set: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            write_set_hash,
            txn_info.state_change_hash(),
            self.write_set,
            expected_write_set,
        );

        let event_hashes = self
            .events()
            .iter()
            .map(CryptoHash::hash)
            .collect::<Vec<_>>();
        let event_root_hash = InMemoryEventAccumulator::from_leaves(&event_hashes).root_hash;
        ensure!(
            event_root_hash == txn_info.event_root_hash(),
            "{}: version:{}, event_root_hash:{:?}, expected:{:?}, events: {:?}, expected(if known): {:?}",
            ERR_MSG,
            version,
            event_root_hash,
            txn_info.event_root_hash(),
            self.events(),
            expected_events,
        );

        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
    }
```

**File:** types/src/transaction/mod.rs (L2261-2284)
```rust
    #[builder(finish_fn = build)]
    pub fn builder_v1(
        transaction_hash: HashValue,
        state_change_hash: HashValue,
        event_root_hash: HashValue,
        state_checkpoint_hash: Option<HashValue>,
        hot_state_checkpoint_hash: Option<HashValue>,
        gas_used: u64,
        status: ExecutionStatus,
        auxiliary_info_hash: Option<HashValue>,
        position_state_checkpoint_hash: Option<HashValue>,
    ) -> Self {
        Self::V1(TransactionInfoV1::new(
            transaction_hash,
            state_change_hash,
            event_root_hash,
            state_checkpoint_hash,
            hot_state_checkpoint_hash,
            gas_used,
            status,
            auxiliary_info_hash,
            position_state_checkpoint_hash,
        ))
    }
```

**File:** types/src/proof/definition.rs (L66-111)
```rust
    /// Verifies an element whose hash is `element_hash` and version is `element_version` exists in
    /// the accumulator whose root hash is `expected_root_hash` using the provided proof.
    pub fn verify(
        &self,
        expected_root_hash: HashValue,
        element_hash: HashValue,
        element_index: u64,
    ) -> Result<()> {
        ensure!(
            self.siblings.len() <= MAX_ACCUMULATOR_PROOF_DEPTH,
            "Accumulator proof has more than {} ({}) siblings.",
            MAX_ACCUMULATOR_PROOF_DEPTH,
            self.siblings.len()
        );

        let actual_root_hash = self
            .siblings
            .iter()
            .fold(
                (element_hash, element_index),
                // `index` denotes the index of the ancestor of the element at the current level.
                |(hash, index), sibling_hash| {
                    (
                        if index % 2 == 0 {
                            // the current node is a left child.
                            MerkleTreeInternalNode::<H>::new(hash, *sibling_hash).hash()
                        } else {
                            // the current node is a right child.
                            MerkleTreeInternalNode::<H>::new(*sibling_hash, hash).hash()
                        },
                        // The index of the parent at its level.
                        index / 2,
                    )
                },
            )
            .0;
        ensure!(
            actual_root_hash == expected_root_hash,
            "{}: Root hashes do not match. Actual root hash: {:x}. Expected root hash: {:x}.",
            type_name::<Self>(),
            actual_root_hash,
            expected_root_hash
        );

        Ok(())
    }
```

**File:** types/src/proof/definition.rs (L864-874)
```rust
    /// Verifies that the `TransactionInfo` exists in the ledger represented by the `LedgerInfo`
    /// at specified version.
    pub fn verify(&self, ledger_info: &LedgerInfo, transaction_version: Version) -> Result<()> {
        verify_transaction_info(
            ledger_info,
            transaction_version,
            &self.transaction_info,
            &self.ledger_info_to_transaction_info_proof,
        )?;
        Ok(())
    }
```

**File:** storage/db-tool/src/replay_on_archive.rs (L388-406)
```rust
        for idx in 0..cur_txns.len() {
            let version = *current_version;
            *current_version += 1;

            if let Err(err) = executed_outputs[idx].ensure_match_transaction_info(
                version,
                &expected_txn_infos[idx],
                Some(&expected_writesets[idx]),
                Some(&expected_events[idx]),
            ) {
                cur_txns.drain(0..idx + 1);
                cur_persisted_aux_info.drain(0..idx + 1);
                expected_txn_infos.drain(0..idx + 1);
                expected_events.drain(0..idx + 1);
                expected_writesets.drain(0..idx + 1);

                return Ok(Some(err));
            }
        }
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L230-235)
```rust
            .prime_state_cache(false)
            .is_block(append_state_checkpoint_to_block.is_some())
            .transaction_info_v1(onchain_config.transaction_info_v1())
            .hot_state_root_in_txn_info(onchain_config.hot_state_root_in_txn_info())
            .compute_trading_native_state_roots(onchain_config.compute_trading_native_state_roots())
            .build()
```

**File:** execution/executor/src/workflow/do_ledger_update.rs (L30-45)
```rust
        // Assemble `TransactionInfo`s. The variant (V0 vs V1) is driven by the
        // `TRANSACTION_INFO_V1` on-chain feature, threaded via
        // `ExecutionOutput::transaction_info_v1`. The hot state root hash a V1 carries is
        // present only when `HOT_STATE_ROOT_IN_TXN_INFO` is also on (`DoStateCheckpoint`
        // produces `Some` hashes iff so); otherwise the V1 leaves it `None`.
        let (transaction_infos, transaction_info_hashes) = Self::assemble_transaction_infos(
            &execution_output.to_commit,
            execution_output.transaction_info_v1,
            &state_checkpoint_output.state_checkpoint_hashes,
            state_checkpoint_output
                .hot_state_checkpoint_hashes
                .as_deref(),
            state_checkpoint_output
                .position_state_checkpoint_hashes
                .as_deref(),
        );
```
