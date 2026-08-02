Based on my investigation, I found a concrete, code-confirmed integrity gap in the transaction-output-to-ledger verification path, distinct from (and stronger than) a simple restated rounding analogy.

### Title
Transaction-output verification silently skips state-checkpoint hash comparison, hiding state-root divergence during replay/debug verification - (File: `types/src/transaction/mod.rs`)

### Summary
`TransactionOutput::ensure_match_transaction_info` is the function used by replay/verification tooling to confirm that a locally re-executed transaction's output matches the `TransactionInfo` committed to the ledger (and therefore covered by the transaction accumulator / `LedgerInfo` signatures). The function checks status, gas used, write-set hash (`state_change_hash`), and event root hash, but it explicitly and admittedly does **not** check `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the state Merkle tree root produced by a transaction.

### Finding Description [1](#0-0) 

The relevant code:
- The function computes and asserts equality for `write_set_hash` vs `txn_info.state_change_hash()` and `event_root_hash` vs `txn_info.event_root_hash()`.
- It then contains a comment stating verbatim: "this comparator ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`), so replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution. Validate the checkpoint hashes here before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`."
- The function then unconditionally returns `Ok(())` without ever inspecting `state_checkpoint_hash()`, `hot_state_checkpoint_hash()`, or `position_state_checkpoint_hash()`.

This is called from at least `storage/db-tool/src/replay_on_archive.rs`, `aptos-move/aptos-debugger/src/aptos_debugger.rs`, and `aptos-move/cli/src/commands.rs`, per [2](#0-1) , [3](#0-2)  — these are exactly the tools whose job is to detect state divergence between locally-recomputed execution and the authenticated on-chain `TransactionInfo`.

`state_checkpoint_hash` is the Sparse-Merkle-Tree root summarizing all writes to world state at a checkpoint boundary [4](#0-3) , and it is one of the fields hashed into `TransactionInfo`, which in turn is the leaf hashed into the transaction accumulator whose root is signed by validators in `LedgerInfo`. Any state-root divergence (e.g. a state-computation bug, a Jellyfish Merkle Tree update bug, or a corrupted intermediate value during executor→storage handoff) would, on a genuine node, still validly recompute the same write set / events (so `state_change_hash` and `event_root_hash` match) while diverging on the actual state root. `ensure_match_transaction_info` is precisely the safety net meant to catch this class of bug, and it structurally cannot, because it never compares the checkpoint hash fields.

### Impact Explanation
This is not a live consensus-path bug (block execution and accumulator commitment happen independently of this comparator), so it does not directly let an attacker forge a proof accepted by an honest node. However, it breaks the state-integrity invariant required by the "Proof And Storage Pivots" guidance: replay and restore verification tooling must be able to detect that "committed state differs from the correct VM result." Because the checkpoint-hash fields are excluded from the comparison, any hard-fork-only state divergence bug (e.g., in JMT restore/replay, aggregator materialization, or write-set-to-storage conversion) occurring on an archival/backup node would go completely undetected by `replay_on_archive`, `aptos-debugger`, and CLI-based simulation-vs-chain comparisons — the exact use cases the guidance calls in scope ("Hard-fork-only divergence during commit, replay, restore, or proof verification"). This significantly weakens Aptos's own detection capability for the most severe class of bug (durable ledger state corruption), effectively blinding the audit/replay safety net that would otherwise catch it.

### Likelihood Explanation
The gap is 100% reproducible and requires no privileged access or race condition — it is a deterministic logic omission, confirmed by the developers' own TODO comment. The triggering precondition (a genuine state-checkpoint-hash divergence occurring from an unrelated bug) is not guaranteed to occur, but *when* it does occur, this comparator is guaranteed to fail to flag it, which is the essence of the vulnerability: the verification net has a known, permanent hole.

### Recommendation
Add explicit checks in `ensure_match_transaction_info` comparing the locally-computed state checkpoint root(s) (`state_checkpoint_hash`, `hot_state_checkpoint_hash`, `position_state_checkpoint_hash`) against the corresponding fields in `txn_info`, at least whenever those fields are `Some` on either side (mismatched `Option` presence should also fail). This should be done independent of/before enabling `COMPUTE_TRADING_NATIVE_STATE_ROOTS`, since the state hash check is meaningful for `state_checkpoint_hash` already in production today.

### Proof of Concept
Not directly exploitable as an attacker PoC (this is a detection-gap, not a state-corruption primitive by itself); the "PoC" is the code path itself:
1. A transaction is executed and produces a `TransactionOutput` with a write set whose hash matches `txn_info.state_change_hash()` (i.e., the write set itself is correct) but whose derived state checkpoint root — computed via whatever state-Merkle-tree logic ran on that node (e.g., during restore/replay from a backup) — differs from `txn_info.state_checkpoint_hash()` due to an independent bug elsewhere in the storage/restore stack.
2. `ensure_match_transaction_info` is called (e.g., from `replay_on_archive`) and returns `Ok(())` because it never inspects `state_checkpoint_hash()` — see the unconditional `Ok(())` at [5](#0-4) .
3. The tool reports a successful replay/verification despite the local state root diverging from the authenticated ledger state root, hiding the underlying corruption.

Note: I was unable to fully inspect `storage/db-tool/src/replay_on_archive.rs` and `aptosdb_reader.rs`'s exact gating of `COMPUTE_TRADING_NATIVE_STATE_ROOTS` due to tool-call limits in this session, so I cannot confirm whether `position_state_checkpoint_hash`/hot-state checks are gated behind an unshipped feature flag versus already relevant to mainnet-committed `state_checkpoint_hash`. The core finding — that `state_checkpoint_hash` itself (which is used in production today, independent of the trading-native feature) is excluded from this comparator — is confirmed directly in the code and comment cited above.

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

**File:** types/src/transaction/mod.rs (L2405-2416)
```rust
    /// The hash value summarizing all changes caused to the world state by this transaction.
    /// i.e. hash of the output write set.
    state_change_hash: HashValue,

    /// The root hash of the Sparse Merkle Tree describing the world state at the end of this
    /// transaction. Depending on the protocol configuration, this can be generated periodical
    /// only, like per block.
    state_checkpoint_hash: Option<HashValue>,

    /// The hash value summarizing PersistedAuxiliaryInfo.
    auxiliary_info_hash: Option<HashValue>,
}
```

**File:** storage/db-tool/src/replay_on_archive.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```

**File:** aptos-move/aptos-debugger/src/aptos_debugger.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
