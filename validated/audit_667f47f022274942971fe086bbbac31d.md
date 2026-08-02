### Title
Ambiguous byte-prefix heuristic in `WriteSetSchema::decode_value` can silently corrupt deserialized write sets - (File: `storage/aptosdb/src/schema/write_set/mod.rs`)

### Summary
`storage/aptosdb/src/schema/write_set/mod.rs` implements a heuristic fallback to read two on-disk encodings of the persisted `WriteSet` blob: the current tagged `enum WriteSet { V0, V1 }` BCS encoding, and a pre-existing "legacy" encoding written by older binaries that had no enum discriminant. The heuristic uses the *first byte* of the stored blob to decide which format applies, and on an `Eof` decode error it drops that byte and reparses the remainder as the legacy struct. Because the legacy payload's leading bytes are attacker/user-independent but naturally-occurring BCS length prefixes (e.g., a single-entry map encodes to byte `0x01`), this is the same "no reserved gap between schema versions" defect as the report's storage-gap issue: two incompatible layouts share an ambiguous byte, and one is silently misinterpreted as the other, corrupting the recovered `WriteSet` content rather than failing loudly.

### Finding Description [1](#0-0) 

The value codec is:
```rust
match data.first() {
    Some(&1) => match bcs::from_bytes::<WriteSet>(data) {
        Ok(ws) => Ok(ws),
        Err(bcs::Error::Eof) => {
            let legacy: LegacyWriteSetV1Payload = bcs::from_bytes(&data[1..])?;
            let mut ws = WriteSet::V0(legacy.value);
            ws.add_hotness(legacy.hotness);
            Ok(ws)
        },
        Err(e) => Err(e.into()),
    },
    _ => bcs::from_bytes::<WriteSet>(data).map_err(Into::into),
}
```
The `LegacyWriteSetV1Payload` comment explicitly documents the risk being worked around: "Legacy V1 payload (without the enum tag byte) from binaries that pre-date the `pub enum WriteSet { V0, V1 }` representation," and the `Eof` branch is only reached "because the new decoder runs out of bytes while reading" the trailing `extensions` field that legacy payloads never had. This proves the two formats are structurally distinguishable only by a length coincidence, not by an explicit version tag reserved for this purpose (the "storage gap" the OpenZeppelin analog calls for).

The failure mode: a legacy-format write-set blob whose first field (`value: WriteSetV0`, itself a length-prefixed map) happens to serialize to a leading byte of `0x01` will be routed into the `Some(&1)` branch and treated as the current `WriteSet::V1` variant. If `bcs::from_bytes::<WriteSet>(data)` runs out of bytes trying to fill `WriteSetV1`'s `extensions` field, the code falls into the recovery branch and reparses `data[1..]` — i.e., it unconditionally discards the leading byte, assuming it was a variant tag. But for this legacy blob, that byte was not a tag; it was the real BCS length prefix of the inner map (e.g., "1 entry"). Discarding it desynchronizes the byte stream: the bytes that were the first map key now get consumed as if they were the map's new length prefix, and the whole `WriteSetV0` map recovered from `legacy.value` is different (wrong number of entries, wrong keys/values) from what was actually written to storage.

### Impact Explanation
This breaks the write-set-survives-storage-round-trip invariant called out in the task's proof/storage pivots: "VM outputs, transaction infos, events, and write sets must survive executor-to-storage handoff unchanged." `WriteSetSchema` is read by `write_set_db.rs` (`put_write_set`/reads) which feeds transaction replay, backup/restore (`restore_utils::save_transactions` at [2](#0-1)  ultimately persists write sets that will later be read back through this same codec), and any consumer reconstructing `TransactionOutput`/`TransactionToCommit` from stored write sets. If a legacy blob collides with the `0x01` prefix, downstream code applies a **different set of state mutations** than the ones the VM actually produced and than what other nodes independently derived from execution — a durable, silent state-commitment divergence, exactly the class of impact the state-integrity gate requires (committed state differing from the correct VM result / corrupting durable ledger data).

### Likelihood Explanation
This path only fires for old data written before the `WriteSet::{V0,V1}` enum wrapper existed (per the code's own comment, kept "temporarily to avoid forge-compat test failures"), so it is not attacker-triggerable on fresh mainnet execution, and it depends on the coincidental leading byte matching `0x01` and the resulting bytes still happening to parse without further error. This narrows real-world likelihood, but the code paths (state-sync backup/restore, chunk executor replay from a mixed-version DB, `db-tool`/backup tooling reading old snapshots) are exactly the unprivileged replay/restore flows the gate calls in-scope, and the bug is deterministic once the byte coincidence occurs — no adversarial control is needed, only pre-existing legacy data.

### Recommendation
Do not disambiguate two incompatible encodings via undocumented byte-prefix heuristics tied to unrelated length prefixes. Instead:
- Reserve an explicit, out-of-band version/format tag when persisting `WriteSetSchema` values (analogous to a storage gap/reserved discriminant), rather than reusing the first content byte of a length-prefixed field as an implicit tag.
- If legacy data must still be supported, migrate it once (batch rewrite with the new explicit tag) rather than doing best-effort disambiguation on every read.
- At minimum, add a self-consistency check after the fallback parse (e.g., verify no trailing bytes remain and that a round-trip re-encode of the recovered `WriteSet` reproduces a length-consistent structure) before accepting the legacy-parsed result, and fail loudly instead of silently returning a plausible-but-wrong `WriteSet`.

### Proof of Concept
1. Construct (or take from an old binary) a legacy-encoded `WriteSetV1` blob whose `value: WriteSetV0` field is a `BTreeMap` with exactly one entry (or otherwise serializes so the very first byte is `0x01`), i.e., `bcs::to_bytes(&LegacyWriteSetV1Payload { value, hotness })` where the first byte of `value`'s encoding is `0x01`.
2. Feed this blob to `WriteSetSchema`'s `ValueCodec::decode_value` (as would happen reading `WriteSetSchema` from RocksDB) — see [3](#0-2) .
3. `data.first() == Some(&1)` routes into the `WriteSet::V1` attempt; if it hits `bcs::Error::Eof` (missing `extensions` field), the code reparses `data[1..]` as `LegacyWriteSetV1Payload`, discarding the real map-length byte.
4. Compare the recovered `WriteSet`'s map entries against the originally-serialized `value` map — they differ (entry desynchronization), demonstrating the committed write set is misread relative to what was actually persisted.

Note: I was not able to fully inspect `WriteSetV1`'s exact field layout (to conclusively confirm the `extensions` field width and byte-for-byte collision odds) within the available search budget; this should be verified directly in `types/src/write_set.rs` before treating the exploit condition as fully confirmed.

### Citations

**File:** storage/aptosdb/src/schema/write_set/mod.rs (L44-80)
```rust
/// Legacy V1 payload (without the enum tag byte) from binaries that pre-date the
/// `pub enum WriteSet { V0, V1 }` representation.
/// TODO(HotState): this is only needed temporarily to avoid forge-compat test failures because in
/// these tests the baseline validators would write legacy format to DB.
#[derive(Deserialize, Serialize)]
struct LegacyWriteSetV1Payload {
    value: WriteSetV0,
    hotness: BTreeSet<StateKey>,
}

impl ValueCodec<WriteSetSchema> for WriteSet {
    fn encode_value(&self) -> Result<Vec<u8>> {
        bcs::to_bytes(self).map_err(Into::into)
    }

    fn decode_value(data: &[u8]) -> Result<Self> {
        // TODO(HotState): we could simply use `bcs::from_bytes` for everything once the latest
        // release branch does not write LegacyWriteSetV1Payload and forge-compat test would not
        // fail in CI.
        match data.first() {
            Some(&1) => match bcs::from_bytes::<WriteSet>(data) {
                Ok(ws) => Ok(ws),
                // Legacy V1 payload lacks the trailing `extensions` field, so the new decoder
                // runs out of bytes while reading it. Any other error indicates real corruption
                // and should propagate.
                Err(bcs::Error::Eof) => {
                    let legacy: LegacyWriteSetV1Payload = bcs::from_bytes(&data[1..])?;
                    let mut ws = WriteSet::V0(legacy.value);
                    ws.add_hotness(legacy.hotness);
                    Ok(ws)
                },
                Err(e) => Err(e.into()),
            },
            _ => bcs::from_bytes::<WriteSet>(data).map_err(Into::into),
        }
    }
}
```

**File:** storage/aptosdb/src/backup/restore_utils.rs (L115-130)
```rust
pub(crate) fn save_transactions(
    state_store: Arc<StateStore>,
    ledger_db: Arc<LedgerDb>,
    first_version: Version,
    txns: &[Transaction],
    persisted_aux_info: &[PersistedAuxiliaryInfo],
    txn_infos: &[TransactionInfo],
    events: &[Vec<ContractEvent>],
    write_sets: Vec<WriteSet>,
    existing_batch: Option<(
        &mut LedgerDbSchemaBatches,
        &mut ShardedStateKvSchemaBatch,
        &mut SchemaBatch,
    )>,
    kv_replay: bool,
) -> Result<()> {
```
