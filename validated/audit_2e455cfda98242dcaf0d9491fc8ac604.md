Based on my investigation, I found a genuine, locally-provable integrity gap.

### Title
`TransactionOutput::ensure_match_transaction_info` omits state-checkpoint / hot-state / native-position root checks, allowing replay-verify to accept a divergent ledger state - ([File: types/src/transaction/mod.rs])

### Summary
`TransactionOutput::ensure_match_transaction_info` [1](#0-0)  is the authenticated-consistency check used to validate that a (fetched or replayed) `TransactionOutput` matches the ledger-committed `TransactionInfo`. It validates status, gas used, write-set hash (`state_change_hash`), and event root hash, but explicitly does **not** validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — a gap the code itself documents as a known TODO.

### Finding Description
The function checks three fields against `txn_info`: status, gas used, and `write_set_hash == txn_info.state_change_hash()`, plus the event root hash [2](#0-1) . It then contains this explicit comment instead of any checkpoint-hash validation: [3](#0-2) 

This means the function never recomputes or compares the state-checkpoint root (the Sparse-Merkle/JMT root produced by applying the write set to prior state), the hot-state checkpoint root, or the `position_state_checkpoint_hash` (native-position tree root, gated by the new `COMPUTE_TRADING_NATIVE_STATE_ROOTS` feature [4](#0-3) ). These roots are the actual state-commitment values bound into `TransactionInfoV1` and hashed into the transaction accumulator [5](#0-4) .

`ensure_match_transaction_info` is called from `aptos-debugger` and the CLI replay-verify path [6](#0-5) , [7](#0-6) , and from `execution/executor/src/chunk_executor/mod.rs` — these are exactly the "replay/verify" tools (e.g. db-tool's `replay_on_archive`) that operators use to confirm a locally re-executed chunk matches the authenticated on-chain `TransactionInfo`.

### Impact Explanation
Because the state/hot-state/position checkpoint hashes aren't compared, a chunk whose write set superficially hashes to the correct `state_change_hash` (i.e., the raw list of write ops matches) but whose *resulting Merkle/JMT root* diverges from the authenticated on-chain root (due to a bug in state-application, JMT construction, or the newly added native-position tree logic) will still pass `ensure_match_transaction_info` and be reported as a successful replay. This directly undermines the proof/commitment-integrity guarantee that replay-verify tooling exists to provide: it can validate the wrong ledger state as correct, masking a hard-fork-class state divergence (especially for the newly introduced native-position state root, which is still being wired in per the TODO and feature-gate comments).

### Likelihood Explanation
This is not exploitable by an external attacker to change consensus-committed state — the accumulator/consensus signature verification elsewhere still binds the correct `TransactionInfo`. The impact is that an internal correctness/monitoring safeguard (replay-verify) silently fails to catch state divergence, which is acknowledged in-code as an open gap tied to the in-progress `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` features. Likelihood of the underlying divergence bug existing is unconfirmed, but the detection gap itself is unambiguous and present in-tree today.

### Recommendation
Extend `ensure_match_transaction_info` to also compare `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` against locally recomputed values whenever those fields are populated in `txn_info`, before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`/`HOT_STATE_ROOT_IN_TXN_INFO` in production, as the code comment itself recommends.

### Proof of Concept
Not independently reproducible from the index alone: this is a code-inspection finding (the check is provably absent per the function body and its own TODO comment), rather than a runtime-exploitable PoC. Confirming actual state divergence would require constructing a chunk whose write set hash matches but whose derived checkpoint root differs, which requires deeper execution-environment access than available here.

### Citations

**File:** types/src/transaction/mod.rs (L2139-2145)
```rust
    pub fn ensure_match_transaction_info(
        &self,
        version: Version,
        txn_info: &TransactionInfo,
        expected_write_set: Option<&WriteSet>,
        expected_events: Option<&[ContractEvent]>,
    ) -> Result<()> {
```

**File:** types/src/transaction/mod.rs (L2148-2195)
```rust
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
```

**File:** types/src/transaction/mod.rs (L2197-2203)
```rust
        // TODO(trading-native): this comparator ignores the checkpoint hashes
        // (state/hot-state and `position_state_checkpoint_hash`), so replay-verify
        // tooling (e.g. db-tool's `replay_on_archive`) can report a successful
        // replay even when the authenticated position state root diverges from
        // local execution. Validate the checkpoint hashes here before enabling
        // COMPUTE_TRADING_NATIVE_STATE_ROOTS.
        Ok(())
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

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L949-955)
```text
    /// When enabled, execution computes the trading-native state roots and commits them to
    /// `TransactionInfoV1`, so they are consensus-verified. Requires `TRANSACTION_INFO_V1`.
    /// Covers the native-position tree today and is intended to cover the other trading-native
    /// trees as they are added. Enabling it first commits the (empty-tree) roots to transaction
    /// info; the actual Move-side writes to those trees are gated by separate flags.
    /// Lifetime: permanent
    const COMPUTE_TRADING_NATIVE_STATE_ROOTS: u64 = 122;
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L1-1)
```rust

```

**File:** aptos-move/cli/src/commands.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
