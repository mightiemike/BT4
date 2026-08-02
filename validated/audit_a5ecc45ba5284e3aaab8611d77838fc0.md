### Title
Wrong accumulator leaf-count passed to `confirm_or_save_frozen_subtrees` during fast-sync snapshot finalization corrupts the persisted transaction accumulator - (File: `storage/aptosdb/src/db/aptosdb_writer.rs`)

### Summary
In `AptosDbWriter::finalize_state_snapshot`, the call that persists the transaction accumulator's frozen-subtree state passes the raw transaction `version` where the accumulator's `num_leaves` (leaf count) is expected. Every other accumulator call site in the codebase consistently uses `version + 1` as the leaf count. This mismatch causes `FrozenSubTreeIterator` to compute the wrong set of node positions for the frozen subtree hashes being written to `TransactionAccumulatorSchema`, which can silently write ledger accumulator nodes at incorrect positions.

### Finding Description
`TransactionAccumulatorDb` consistently treats "leaf count" as `version + 1` everywhere it derives one from a version: [1](#0-0) [2](#0-1) 

However, in `finalize_state_snapshot` (used during fast/state-sync to bootstrap a node directly at a target version), the code calls: [3](#0-2) 

Here `version` (the version of the single transaction being finalized, not `version + 1`) is passed directly as the `num_leaves` argument to `restore_utils::confirm_or_save_frozen_subtrees`: [4](#0-3) 

Inside, `num_leaves` is used to derive the expected list of frozen-subtree node `Position`s via `FrozenSubTreeIterator::new(num_leaves)`, and these positions are paired one-to-one with the `frozen_subtrees` hash list supplied by the caller (`ledger_info_to_transaction_infos_proof.left_siblings()`, i.e. the real frozen subtrees at `version` leaves, i.e., leaf count `version + 1`). Because `FrozenSubTreeIterator`'s output depends on the popcount/bit-pattern of the leaf count, using `version` instead of `version + 1` computes a *different* set of tree positions whenever `popcount(version) == popcount(version + 1)` (this occurs whenever `version`'s lowest set-bit run is exactly one trailing `1`, e.g. `version` ≡ 1 mod 4, roughly 1 in 4 versions). In those cases the `ensure!(positions.len() == frozen_subtrees.len())` check at `restore_utils.rs:85-90` passes (list lengths coincidentally match) but the actual `Position` values written are wrong for the correct leaf count, so real transaction-accumulator hashes get written at positions in `TransactionAccumulatorSchema` that don't correspond to the true accumulator shape for that version. For the majority of other versions, the lengths differ and the call fails loudly (`ensure!` error), which is a correctness/DoS issue during fast sync, but not silent corruption.

### Impact Explanation
`TransactionAccumulatorSchema` entries are later read back by `TransactionAccumulatorDb::get_frozen_subtree_hashes`/`get_transaction_proof`/`get_consistency_proof` to compute the accumulator root and to serve transaction/consistency proofs to peers and light clients. If frozen-subtree hashes are stored at incorrect positions during snapshot finalization (fast sync), the locally computed accumulator root can diverge from the true ledger accumulator root for the target version, and any proofs subsequently served from this node (transaction proof, consistency proof) would be internally consistent with the corrupted local state but not with the real chain state — i.e., an authenticated proof response bound to the wrong root/version. This falls squarely under "Wrong accumulator root ... accepted as valid" and "authenticated API ... bound to the wrong version/root" in the state-integrity gate.

### Likelihood Explanation
This path only triggers during `finalize_state_snapshot`, which is invoked by the fast-sync / state-snapshot bootstrapping flow (single transaction output+info restore). It requires no attacker privilege — it is a deterministic function-of-version bug that fires whenever a node fast-syncs and the target version's bit pattern satisfies `popcount(version) == popcount(version+1)` (silent corruption case) or otherwise causes a hard failure (`ensure!`) for other versions (availability/robustness issue). Because target versions are effectively arbitrary (chosen by whatever epoch/version the node syncs to), this is not an edge case restricted to unusual input — a meaningful fraction of possible sync target versions hit the silent-corruption branch.

### Recommendation
Change the call in `finalize_state_snapshot` to pass `version + 1` (or equivalently `version.saturating_add(1)`) as the leaf count, matching the convention used by every other `Accumulator`/`TransactionAccumulatorDb` call site (e.g. `get_transaction_proof`, `get_consistency_proof`):
```rust
restore_utils::confirm_or_save_frozen_subtrees(
    self.ledger_db.transaction_accumulator_db_raw(),
    version + 1,
    frozen_subtrees,
    None,
)?;
```

### Proof of Concept
Not independently executed (no filesystem/terminal access in this session; this is a static-analysis finding). To reproduce:
1. Drive a fast-sync/state-snapshot bootstrap (`finalize_state_snapshot`) to a target `version` such that `popcount(version) == popcount(version + 1)` (e.g. `version = 1` → both have popcount 1; `version = 5` (`0b101`) vs `6` (`0b110`) both popcount 2).
2. Observe that `confirm_or_save_frozen_subtrees` is called with `num_leaves = version` instead of `version + 1`, passes its internal length `ensure!`, but writes frozen subtree hashes at `Position`s derived from `FrozenSubTreeIterator::new(version)` rather than the correct `FrozenSubTreeIterator::new(version + 1)`.
3. Subsequently call `get_frozen_subtree_hashes(version + 1)` / `get_transaction_proof` and compare the resulting accumulator root against the `LedgerInfo`'s `transaction_accumulator_hash` for that version — they would not correspond to a correctly reconstructed accumulator, though full confirmation requires running the actual restore code path, which this environment cannot execute.

**Note on confidence:** I was unable to run the code or compare directly against upstream `aptos-core` history in this session (the repo only shows a single "Initial commit" for this file, so I could not confirm via blame whether this line differs from the canonical upstream implementation). The finding is based on a clear internal inconsistency (this call site uses `version` while all sibling call sites use `version + 1` for the same concept), which is a strong but not 100%-verified signal of a genuine bug versus an intentional but confusingly-named parameter. I recommend a Devin session with full repo/build access to confirm by diffing against upstream `aptos-core` and/or writing a unit test that fast-syncs to a version satisfying the popcount condition and asserts the persisted accumulator root.

### Citations

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L65-73)
```rust
    /// Returns proof for transaction at `version` towards root of ledger at `ledger_version`.
    pub fn get_transaction_proof(
        &self,
        version: Version,
        ledger_version: Version,
    ) -> Result<TransactionAccumulatorProof> {
        Accumulator::get_proof(self, ledger_version + 1 /* num_leaves */, version)
            .map_err(Into::into)
    }
```

**File:** storage/aptosdb/src/ledger_db/transaction_accumulator_db.rs (L94-105)
```rust
    pub fn get_consistency_proof(
        &self,
        client_known_version: Option<Version>,
        ledger_version: Version,
    ) -> Result<AccumulatorConsistencyProof> {
        let client_known_num_leaves = client_known_version
            .map(|v| v.saturating_add(1))
            .unwrap_or(0);
        let ledger_num_leaves = ledger_version.saturating_add(1);
        Accumulator::get_consistency_proof(self, ledger_num_leaves, client_known_num_leaves)
            .map_err(Into::into)
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L170-180)
```rust
            // Update the merkle accumulator using the given proof
            let frozen_subtrees = output_with_proof
                .proof
                .ledger_info_to_transaction_infos_proof
                .left_siblings();
            restore_utils::confirm_or_save_frozen_subtrees(
                self.ledger_db.transaction_accumulator_db_raw(),
                version,
                frozen_subtrees,
                None,
            )?;
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L78-90)
```rust
pub fn confirm_or_save_frozen_subtrees(
    transaction_accumulator_db: &DB,
    num_leaves: LeafCount,
    frozen_subtrees: &[HashValue],
    existing_batch: Option<&mut SchemaBatch>,
) -> Result<()> {
    let positions: Vec<_> = FrozenSubTreeIterator::new(num_leaves).collect();
    ensure!(
        positions.len() == frozen_subtrees.len(),
        "Number of frozen subtree roots not expected. Expected: {}, actual: {}",
        positions.len(),
        frozen_subtrees.len(),
    );
```
