Based on my investigation, I found a genuine architectural analog worth flagging, but I could not fully verify a concrete corrupted value/exploit path within the tool budget available. I'm reporting what I found with explicit caveats rather than overclaiming.

### Title
Native-position writes are excluded from the main state root while position-tree root computation is optional/non-deterministic across nodes - ([File: storage/aptosdb/src/db/aptosdb_writer.rs])

### Summary
The repository has added a "trading-native" position subsystem that stores `StateKey::TradingNative::Position` entries in a completely separate KV store and Jellyfish Merkle tree (`PositionDb`/`PositionMerkleDb`), parallel to, and disjoint from, the main state tree — analogous to two "layers" (L1/L2) each with their own idea of committed state for what should be one logical ledger. Writes for these keys are routed via `Extension::NativePosition` and consumed only through `WriteSet::native_position_iter`, and are explicitly excluded from `state_update_refs`/`write_op_iter`, which feed the main Jellyfish Merkle state tree and its root hash [1](#0-0) . The commit path computes the position root either from `chunk.position_state_summary` (if present) or by calling `self.position_summary_at_commit(chunk)` locally when absent [2](#0-1) .

### Finding Description
The design mirrors the report's root cause structurally: state for the "position" domain is tracked in an entirely separate merklized store from the main state tree, with an implicit invariant that a given logical key space is exclusively owned by one tree or the other (`StateKeyInner::TradingNative` is carved out of `state_update_refs()` at the `WriteSetMut`/value-writes level, so it never reaches the main SMT/JMT) [3](#0-2) . The `commit_native_position` path conditionally recomputes the position-tree summary locally (`self.position_summary_at_commit(chunk)`) when the chunk didn't carry one, rather than always deriving it from a value that is verified against consensus-agreed data the way the main state root is checked against `LedgerInfo` in `check_and_put_ledger_info` [4](#0-3) . I was not able to trace, within the available budget, whether `chunk.position_state_summary` is itself authenticated by consensus/`LedgerInfo` (e.g., embedded in `TransactionInfoV1`'s `position_state_checkpoint_hash` field I saw in `TransactionInfo::builder_v1`) or whether the locally-recomputed fallback could silently diverge from what other validators compute, producing an unauthenticated or inconsistent root that isn't checked the same way the main accumulator/state root is in `check_and_put_ledger_info`.

### Impact Explanation
If the position-tree root is not uniformly bound to `LedgerInfo`/consensus the same way the main accumulator and state root are, a divergence here would not be caught by the existing root-hash consistency check I found (`check_and_put_ledger_info`), which only validates the main `transaction_accumulator_hash`, not any position-tree root. That would let a node commit a different position-tree state than the rest of the network while still passing existing consistency checks — a state-commitment integrity break analogous to the bridge bug (two conflicting views of committed state, no cross-check preventing divergence).

### Likelihood Explanation
I could not confirm this is exploitable or even reachable on mainnet within my research budget: the subsystem is gated behind `ENABLE_TRADING_NATIVE` / `COMPUTE_TRADING_NATIVE_STATE_ROOTS` flags (seen in `aptosdb_reader.rs`), and I did not locate the code that ties `position_state_checkpoint_hash` (seen only as a field name in `TransactionInfoV1::builder_v1`) into consensus voting/signature verification, nor the code that decides when `chunk.position_state_summary` is `Some` vs `None`. Without that trace, I cannot confirm whether the "locally recomputed" fallback path can actually diverge from a canonical value in a way that a malicious or buggy node could exploit to commit wrong state while remaining accepted by peers.

### Recommendation
This requires further investigation before treating it as confirmed:
1. Trace whether `TransactionInfoV1.position_state_checkpoint_hash` is signed/voted on by consensus and enforced identically to `state_checkpoint_hash`.
2. Confirm whether `chunk.position_state_summary` is always populated by execution (making the local-recompute fallback dead code) or can legitimately be `None` on a validated path, and if so, whether the recomputed value is checked against any authenticated value before being persisted.
3. Add an explicit root-hash consistency check for the position tree analogous to `check_and_put_ledger_info`'s main-accumulator check, if none currently exists.

### Proof of Concept
Not constructed — I was unable to confirm within the available tool budget whether `position_state_summary` can actually diverge from a consensus-agreed value, so I cannot provide a concrete reproducing sequence. I recommend a Devin session with broader read/search access to trace `position_state_checkpoint_hash` through consensus signing/verification code (`consensus/`, `execution/executor`) and the exact call sites that set `chunk.position_state_summary` before concluding whether this is a real, exploitable divergence.

### Citations

**File:** types/src/write_set.rs (L504-509)
```rust
/// Native-position write produced by a transaction. Type-distinct from [`WriteOp`] so the compiler
/// refuses to mix native-position entries into the main-state bucket.
///
/// Carried inside [`Extension::NativePosition`] on a `WriteSet`. The storage commit applier (in
/// `aptos-db`) consumes it via [`WriteSet::native_position_iter`].
#[derive(Clone, Debug, Eq, PartialEq)]
```

**File:** types/src/write_set.rs (L667-672)
```rust
    pub fn state_update_refs(&self) -> impl Iterator<Item = (&StateKey, Option<&StateValue>)> + '_ {
        self.value_writes()
            .write_set
            .iter()
            .map(|(key, op)| (key, op.as_state_value_opt()))
    }
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L384-389)
```rust
        // it here so the tree still tracks forward (not consensus-committed).
        if let Some(store) = bundle.state_store.as_ref() {
            let new_state = match chunk.position_state_summary {
                Some(summary) => summary.clone(),
                None => self.position_summary_at_commit(chunk)?,
            };
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L703-732)
```rust
    fn check_and_put_ledger_info(
        &self,
        version: Version,
        ledger_info_with_sig: &LedgerInfoWithSignatures,
        ledger_batch: &mut SchemaBatch,
    ) -> Result<(), AptosDbError> {
        let ledger_info = ledger_info_with_sig.ledger_info();

        // Verify the version.
        ensure!(
            ledger_info.version() == version,
            "Version in LedgerInfo doesn't match last version. {:?} vs {:?}",
            ledger_info.version(),
            version,
        );

        // Verify the root hash.
        let db_root_hash = self
            .ledger_db
            .transaction_accumulator_db()
            .get_root_hash(version)?;
        let li_root_hash = ledger_info_with_sig
            .ledger_info()
            .transaction_accumulator_hash();
        ensure!(
            db_root_hash == li_root_hash,
            "Root hash pre-committed doesn't match LedgerInfo. pre-commited: {:?} vs in LedgerInfo: {:?}",
            db_root_hash,
            li_root_hash,
        );
```
