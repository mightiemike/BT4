### Title
Silently dropped write-set changes in authenticated `TransactionInfo.changes` API response due to ignored conversion error - ([File: api/types/src/convert.rs])

### Summary
`into_transaction_info` in the REST API converter builds the `changes` field of every returned `TransactionInfo` by iterating the transaction's committed `WriteSet` and calling `try_into_write_set_changes` per entry, but silently discards any entry that fails to convert via `.ok()` instead of propagating the error.

### Finding Description
In `into_transaction_info`:
```rust
changes: write_set
    .into_write_op_iter()
    .filter_map(|(sk, wo)| self.try_into_write_set_changes(sk, wo).ok())
    .flatten()
    .collect(),
``` [1](#0-0) 

`try_into_write_set_changes` returns `Err` for `StateKeyInner::TradingNative(_)` (and `StateKeyInner::Raw(_)`) keys:
```rust
StateKeyInner::TradingNative(_) => Err(format_err!(
    "Can't convert trading-native key {:?} to WriteSetChange",
    state_key
)),
``` [2](#0-1) 

`TradingNativeKey::Position` is a real, production write-set entry type (not test-only): it is written by the native-position subsystem, iterated via `write_set().native_position_iter()`, and persisted/committed as part of normal transaction execution and storage commit. [3](#0-2) 
Its state root is even consensus-verified through `TransactionInfoV1.position_state_checkpoint_hash` when `COMPUTE_TRADING_NATIVE_STATE_ROOTS` is enabled. [4](#0-3) 

Because the conversion error for such a write op is swallowed by `.ok()` and `filter_map`, that write entry is simply dropped from the `changes` vector returned to API clients — with no error, no log visible to the caller, and no indication that data is missing. `state_change_hash` in the same `TransactionInfo` is computed independently from the raw write set (not from the JSON `changes` list), so this omission is not detectable by comparing hashes in the response; the returned `TransactionInfo.changes` silently diverges from the actual committed write set while still appearing to be a validly-hashed, authenticated representation of it.

By contrast, the sibling function `try_into_write_set_payload` (used for genesis Direct write sets) correctly propagates the same error via `.collect::<Result<Vec<Vec<_>>>>()?`, confirming that dropping the error in `into_transaction_info` is an inconsistency/bug rather than intended behavior. [5](#0-4) 

### Impact Explanation
This is an authenticated API/state-view correctness issue: any client of the REST API's transaction endpoints (e.g., `GetTransactionByVersion/Hash`, which route through `try_into_onchain_transaction` → `into_transaction_info`) can silently receive a `TransactionInfo.changes` list that omits real, committed state writes whenever the write set contains keys the converter cannot decode (currently `TradingNative` position writes, and any other future/unknown `StateKeyInner` variant). Indexers, bridges, wallets, or auditing tools that rely on the API's `changes` field to reconstruct or verify on-chain state will get an incomplete/incorrect view of committed ledger data without any error signal, even though the transaction genuinely modified that state on mainnet.

### Likelihood Explanation
This is not an attacker-controlled corruption of consensus state (the underlying committed ledger/state and accumulator remain correct), so it does not violate the state-commitment gate for the executor/storage path itself. However, it does violate the "authenticated API or state-view output bound to the wrong version/object" pivot for any transaction whose write set includes a `TradingNative` (or `Raw`) state key — which occurs automatically and deterministically whenever `NATIVE_POSITION`/`TRADING_NATIVE` features are enabled and a user transaction updates a trading position. No malicious input is required; it triggers on ordinary, feature-gated production traffic.

### Recommendation
In `into_transaction_info`, do not silently drop conversion errors. At minimum, log/count skipped entries distinctly from valid omissions, or better, propagate the error (mirroring `try_into_write_set_payload`) so callers get an explicit 5xx/error instead of a falsely complete list; alternatively, add an explicit, documented placeholder `WriteSetChange` variant for state-key kinds that cannot be represented in the public API schema, so the presence of such a write is at least visible in the response instead of vanishing.

### Proof of Concept
1. Enable `TRADING_NATIVE`, `NATIVE_POSITION`, `TRANSACTION_INFO_V1`, and `HOTNESS_IN_EPILOGUE` features (as gated in `types/src/on_chain_config/aptos_features.rs`/`features.move`).
2. Submit a transaction that writes a native position (`aptos_trading::native_position_types::Position`), producing a `WriteSet` entry keyed by `StateKeyInner::TradingNative(TradingNativeKey::Position{..})`, as committed in `commit_native_position`.
3. Query the transaction via the REST API (`GET /transactions/by_hash/{hash}` or `/by_version/{version}`), which calls `try_into_onchain_transaction` → `into_transaction_info`.
4. Observe that the returned `TransactionInfo.changes` array does not contain any entry corresponding to the position write, even though it was committed to storage and reflected in `state_change_hash`/`position_state_checkpoint_hash`, because `try_into_write_set_changes` returned `Err` for the `TradingNative` key and `.ok()` dropped it silently.

### Citations

**File:** api/types/src/convert.rs (L311-315)
```rust
            changes: write_set
                .into_write_op_iter()
                .filter_map(|(sk, wo)| self.try_into_write_set_changes(sk, wo).ok())
                .flatten()
                .collect(),
```

**File:** api/types/src/convert.rs (L579-589)
```rust
                let nested_writeset_changes: Vec<Vec<WriteSetChange>> = write_set
                    .into_write_op_iter()
                    .map(|(state_key, op)| self.try_into_write_set_changes(state_key, op))
                    .collect::<Result<Vec<Vec<_>>>>()?;
                WriteSetPayload {
                    write_set: WriteSet::DirectWriteSet(DirectWriteSet {
                        // TODO: the resource value is interpreted by the type definition at the version of the converter, not the version of the tx: must be fixed before we allow module updates
                        changes: nested_writeset_changes
                            .into_iter()
                            .flatten()
                            .collect::<Vec<WriteSetChange>>(),
```

**File:** api/types/src/convert.rs (L618-621)
```rust
            StateKeyInner::TradingNative(_) => Err(format_err!(
                "Can't convert trading-native key {:?} to WriteSetChange",
                state_key
            )),
```

**File:** storage/aptosdb/src/db/aptosdb_writer.rs (L359-376)
```rust
        for (i, output) in chunk.transaction_outputs.iter().enumerate() {
            let version = chunk_first + i as Version;
            let position_writes: Vec<_> = output
                .write_set()
                .native_position_iter()
                .map(|(k, op)| (k.clone(), op.as_write_op().clone()))
                .collect();
            if !position_writes.is_empty() {
                committer
                    .apply(
                        version,
                        position_writes,
                        &mut sharded_kv_batches,
                        &mut in_chunk_prior,
                    )
                    .map_err(|e| AptosDbError::Other(format!("native commit: {e}")))?;
            }
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
