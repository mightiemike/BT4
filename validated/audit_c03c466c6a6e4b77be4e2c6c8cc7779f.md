### Title
`TransactionOutput::ensure_match_transaction_info` skips state-checkpoint root validation, letting replay-verify accept a divergent authenticated state root - (File: types/src/transaction/mod.rs)

### Summary
`TransactionOutput::ensure_match_transaction_info`, the integrity check used by the replay-verification tooling, only compares status, gas used, write-set hash, and event root hash against the archived `TransactionInfo`. It never compares `state_checkpoint_hash`, `hot_state_checkpoint_hash`, or `position_state_checkpoint_hash` — the fields that authenticate the actual Sparse-Merkle/Jellyfish state root committed to the ledger. This is called out in-code as a known TODO, but the gap is real and reachable today via `db-tool replay-on-archive`.

### Finding Description
`ensure_match_transaction_info` is the function that asserts a freshly re-executed `TransactionOutput` matches a `TransactionInfo` retrieved from an archived/backup source of truth: [1](#0-0) 

It validates `status`, `gas_used`, and the write-set hash against `state_change_hash`, and the event root hash, but explicitly documents — via a `TODO(trading-native)` comment — that it "ignores the checkpoint hashes (state/hot-state and `position_state_checkpoint_hash`)", meaning "replay-verify tooling (e.g. db-tool's `replay_on_archive`) can report a successful replay even when the authenticated position state root diverges from local execution": [2](#0-1) 

`TransactionInfo` carries `state_checkpoint_hash`, and (in `TransactionInfoV1`) `hot_state_checkpoint_hash` and `position_state_checkpoint_hash`, which are the actual committed Sparse Merkle Tree / Jellyfish Merkle Tree state roots at checkpoint boundaries: [3](#0-2) [4](#0-3) 

This check is directly wired into `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::execute_and_verify`, which is the primary mainnet operational tool used to confirm that re-executing an archived transaction range against a backup produces the correct committed ledger state. It calls `ensure_match_transaction_info` per transaction and treats an `Ok(())` result as full verification success: [5](#0-4) 

Because the state-checkpoint root fields are excluded from the comparison, any divergence confined to the state tree itself — e.g., a corrupted/incorrect state value written to storage, a bug in Jellyfish Merkle tree construction, or a tampered/incorrect `state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` in the source `TransactionInfo` stream used for verification — will not be detected as long as the write-set bytes, events, gas, and status still match. The write-set hash check only proves the write set content is byte-identical to what was fed in; it does not prove that this write set, when applied to the JMT/hot-state/position-state trees, produces the state root that the network actually committed and that light clients/full nodes rely on for proofs.

### Impact Explanation
This breaks a core proof/commitment invariant: the guarantee that the state-checkpoint root recorded in the authenticated `TransactionInfo` reflects the actual Merkle-tree state produced by applying the write set. `db-tool replay-on-archive` is the standard mechanism operators and infrastructure providers use to confirm that a chain's backup/archive is trustworthy and that historical state matches what full nodes would recompute — this underpins fast-sync/restore trust and dispute resolution around ledger integrity. A silent gap here means a corrupted or malicious state root embedded in an archived `TransactionInfo` stream (or a storage-layer bug that corrupts committed state) can pass verification undetected, i.e., "committed state that differs from the correct VM result... accepted as valid" per the state-commitment invariant. This is a high-severity integrity gap because it defeats the intended safety net for state-root correctness during replay/restore-adjacent tooling, though it does not directly forge on-chain consensus (which computes/verifies its own commit info independently via the executor pipeline).

### Likelihood Explanation
The unprivileged trigger is straightforward: any user of `replay_on_archive` supplying (or relying on) a backup source whose `TransactionInfo.state_checkpoint_hash`/`hot_state_checkpoint_hash`/`position_state_checkpoint_hash` do not match true re-executed state (due to storage bug, backup corruption, or tampering by whoever controls the backup files) will get a false "pass" as long as the write set bytes themselves are unmodified. The bug is deterministic and 100% reproducible; it requires no privileged access to trigger the false-positive result, only a corrupted/mismatched `TransactionInfo` stream, which is exactly the input replay-verify exists to validate.

### Recommendation
Extend `ensure_match_transaction_info` to also validate `state_checkpoint_hash`, `hot_state_checkpoint_hash`, and `position_state_checkpoint_hash` by re-deriving the actual state/hot-state/position Merkle roots produced from applying the transaction's write set at the given version and comparing them against the `TransactionInfo` fields, before allowing `COMPUTE_TRADING_NATIVE_STATE_ROOTS`-style features to be considered safe for replay-verify use, as the existing TODO already flags.

### Proof of Concept
1. Take an archived/backup `TransactionInfo` at some checkpoint version and mutate only `state_checkpoint_hash` (or `hot_state_checkpoint_hash`/`position_state_checkpoint_hash`) to an incorrect value while leaving `state_change_hash` (write-set hash), `event_root_hash`, `gas_used`, and `status` untouched.
2. Run `storage/db-tool/src/replay_on_archive.rs`'s `Verifier::verify` over that version range; `execute_and_verify` calls `ensure_match_transaction_info` per transaction: [6](#0-5) 
3. Because `ensure_match_transaction_info` never reads `state_checkpoint_hash()`/`hot_state_checkpoint_hash()`/`position_state_checkpoint_hash()` (confirmed by inspecting its full body): [7](#0-6)  the call returns `Ok(())`, and the replay tool reports the range as successfully verified despite the state-checkpoint root being wrong.

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

**File:** types/src/transaction/mod.rs (L2336-2364)
```rust
    pub fn state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(v) => v.state_checkpoint_hash,
            Self::V1(v) => v.state_checkpoint_hash,
        }
    }

    pub fn has_state_checkpoint_hash(&self) -> bool {
        self.state_checkpoint_hash().is_some()
    }

    pub fn ensure_state_checkpoint_hash(&self) -> Result<HashValue> {
        self.state_checkpoint_hash()
            .ok_or_else(|| format_err!("State checkpoint hash not present in TransactionInfo"))
    }

    pub fn hot_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.hot_state_checkpoint_hash,
        }
    }

    pub fn position_state_checkpoint_hash(&self) -> Option<HashValue> {
        match self {
            Self::V0(_) => None,
            Self::V1(v) => v.position_state_checkpoint_hash,
        }
    }
```

**File:** types/src/transaction/mod.rs (L2440-2461)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct TransactionInfoV1 {
    gas_used: u64,
    status: ExecutionStatus,
    transaction_hash: HashValue,
    event_root_hash: HashValue,
    state_change_hash: HashValue,
    state_checkpoint_hash: Option<HashValue>,
    hot_state_checkpoint_hash: Option<HashValue>,
    auxiliary_info_hash: Option<HashValue>,

    /// Repurposed reserved field; `None` matches the prior BCS encoding.
    position_state_checkpoint_hash: Option<HashValue>,
    placeholder1: Option<HashValue>,
    placeholder2: Option<HashValue>,
    placeholder3: Option<HashValue>,
    placeholder4: Option<HashValue>,
    placeholder5: Option<HashValue>,
    placeholder6: Option<HashValue>,
    placeholder7: Option<HashValue>,
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
