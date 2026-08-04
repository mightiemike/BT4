## Title
State-sync's size-aware transaction output serving path discards persisted `TransactionAuxiliaryData`, causing divergent VM-output representations for the same version - (File: `state-sync/storage-service/server/src/storage.rs`)

### Summary
`StorageReader::get_transaction_outputs_with_proof_by_size` (the size/time-aware chunking path used to serve `get_transaction_outputs_with_proof` requests) constructs each `TransactionOutput` with a hardcoded `TransactionAuxiliaryData::None`, instead of reading the value persisted in `transaction_auxiliary_data_db` for that version. [1](#0-0) 

By contrast, the canonical `AptosDbReader::get_transaction_outputs` implementation reads the actual persisted auxiliary data via `self.ledger_db.transaction_auxiliary_data_db().get_transaction_auxiliary_data(version)?.unwrap_or_default()` and uses that value to build the `TransactionOutput`. [2](#0-1) 

### Finding Description
For a given committed version whose VM execution produced non-default `TransactionAuxiliaryData` (persisted in `transaction_auxiliary_data_db`), two "authoritative" code paths that both claim to expose the accumulator-proven `TransactionOutput` for that version return different `auxiliary_data` fields:

- The direct storage path (`AptosDbReader::get_transaction_outputs`, `storage/aptosdb/src/db/aptosdb_reader.rs:380-391`) reads and returns the real, persisted `TransactionAuxiliaryData`.
- The state-sync storage-service serving path used for `get_transaction_outputs_with_proof_by_size` (`state-sync/storage-service/server/src/storage.rs:644-650`) unconditionally substitutes `TransactionAuxiliaryData::None`, discarding whatever was actually persisted.

Both paths attach the *same* `TransactionInfo` / accumulator proof (`transaction_info_iterator` / `transaction_accumulator_db` in both cases), so a client cannot distinguish the two responses as "different versions" — they are proof-bound to the identical `TransactionInfo` and root, yet carry different `TransactionOutput.auxiliary_data` values. This breaks the invariant that all proof-bearing serving paths for the same version must expose identical VM-output fields.

### Impact Explanation
This creates two mutually inconsistent representations of the VM output for the same accumulator-proven version, served to different classes of consumers (state-sync peers pulling `TransactionOutputListWithProofV2` chunks vs. any local/direct consumer of `AptosDbReader`). Any downstream logic that inspects `auxiliary_data` (e.g., replay/diff tooling in `aptos-move/replay-benchmark/src/diff.rs`, or future consumers relying on this field) would see different, inconsistent data depending on which path served it — despite both being "proof-verified" against the same `TransactionInfo`. This is a real data-integrity inconsistency in a proof-bearing serving path, though its blast radius is currently limited by the fact that `TransactionAuxiliaryData` is largely unused/deprecated in practice (as suggested by the inline comment "Auxiliary data is no longer supported").

### Likelihood Explanation
This triggers deterministically whenever: (1) `use_size_and_time_aware_chunking` is enabled for the size-aware path, and (2) the requested version has a non-default persisted `TransactionAuxiliaryData`. Given the comment in the code ("Auxiliary data is no longer supported"), it's plausible that in current VM execution no version actually produces non-default auxiliary data anymore, which would make this a latent/dead-code inconsistency rather than an actively exploitable one today. I could not fully verify from available code whether any current or historical mainnet transaction actually sets non-default `TransactionAuxiliaryData` in `types/src/transaction/mod.rs`, nor could I inspect the legacy sibling function (`get_transaction_outputs_with_proof_by_size_legacy`) to confirm whether it has the same bug or correctly reads from storage — that comparison would clarify whether this is a newly introduced regression specific to the size-aware path.

### Recommendation
In `state-sync/storage-service/server/src/storage.rs`, extend the `multizip_iterator` in `get_transaction_outputs_with_proof_by_size` to also zip in `self.storage.get_auxiliary_data_iterator(...)` (or the equivalent accessor backed by `transaction_auxiliary_data_db`), and use that value instead of the hardcoded `TransactionAuxiliaryData::None` when constructing `TransactionOutput`, mirroring the approach in `AptosDbReader::get_transaction_outputs`.

### Proof of Concept
1. Commit a transaction at version `V` that produces non-default `TransactionAuxiliaryData` (persisted into `transaction_auxiliary_data_db`).
2. Call `AptosDbReader::get_transaction_outputs(V, 1, V)` directly — observe `TransactionOutput.auxiliary_data` reflects the real persisted value (`storage/aptosdb/src/db/aptosdb_reader.rs:380-391`).
3. Call the storage-service's `get_transaction_outputs_with_proof_by_size` for the same version range with `use_size_and_time_aware_chunking = true` — observe the returned `TransactionOutput.auxiliary_data` is always `TransactionAuxiliaryData::None` (`state-sync/storage-service/server/src/storage.rs:644-650`), despite carrying an accumulator proof bound to the same `TransactionInfo` at version `V`.
4. Assert the two `auxiliary_data` fields differ for identical `TransactionInfo`/version.

Note: I was unable to confirm within this review whether current VM execution paths (in `types/src/transaction/mod.rs`'s `TransactionAuxiliaryData` producers) ever actually emit a non-`None` variant under normal mainnet operation, nor did I get to inspect `get_transaction_outputs_with_proof_by_size_legacy` for comparison — these are open items that affect real-world exploitability versus latent/dead-code status.

### Citations

**File:** state-sync/storage-service/server/src/storage.rs (L644-650)
```rust
                    let output = TransactionOutput::new(
                        write_set,
                        events,
                        info.gas_used(),
                        info.status().clone().into(),
                        TransactionAuxiliaryData::None, // Auxiliary data is no longer supported
                    );
```

**File:** storage/aptosdb/src/db/aptosdb_reader.rs (L380-391)
```rust
                    let auxiliary_data = self
                        .ledger_db
                        .transaction_auxiliary_data_db()
                        .get_transaction_auxiliary_data(version)?
                        .unwrap_or_default();
                    let txn_output = TransactionOutput::new(
                        write_set,
                        events,
                        txn_info.gas_used(),
                        txn_info.status().clone().into(),
                        auxiliary_data,
                    );
```
