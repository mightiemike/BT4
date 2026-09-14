### Title
Per-receipt storage-proof limit only bounds `FunctionCall` actions, letting non-FunctionCall actions in the same receipt push the recorded storage witness past the configured cap - ([File: runtime/runtime/src/lib.rs])

### Summary
This is analogous to the Notional bug class: a limit/fee-relevant computation ("prime vault fee") is only assessed against one component of a multi-component quantity (primary debt), letting a user shift value into the un-checked component (secondary debt) to evade the limit. In nearcore, the per-receipt storage-proof-recording limit is similarly only enforced against one action kind (`FunctionCall`, via `RecordedStorageCounter` inside the VM), while other action kinds executed in the *same* receipt are not checked against the limit at all, so their contribution to the state-witness storage proof can push the total past the intended cap.

### Finding Description
`RecordedStorageCounter` enforces `size_limit` only through `observe_size`, which is invoked from inside the WASM VM logic during `FunctionCall` host-function trie accesses [1](#0-0) . This counter has no visibility into the storage-proof growth caused by other action kinds (e.g. `DeployContract`, `DeleteAccount`, `AddKey`, `DeleteKey`, `Stake`, `CreateAccount`, global-contract actions) that touch the trie for storage usage recalculation, key/account manipulation, etc.

This exact gap is documented as a known, still-open limitation being tracked for correction by the `EnforceStorageProofLimitForAllActions` protocol feature [2](#0-1) . The comment for this feature states explicitly: *"The `RecordedStorageCounter` only runs inside the VM, so it bounds `FunctionCall` actions alone; other actions in the same receipt could record proof past the limit."* The intended fix — checking the receipt's recorded size after each action and failing with `ActionErrorKind::ReceiptStorageProofSizeExceeded` once it goes over — is not yet applied per-action for non-`FunctionCall` actions in the current code path (only `EnforcePerReceiptStorageProofLimit` gates the general limit, and the enforcement mechanism is VM-scoped to `FunctionCall`).

A transaction signer can construct receipts containing many non-`FunctionCall` actions (e.g., large batches of `AddKey`/`DeleteKey`, `DeployContract`, or `CreateAccount`/`DeleteAccount` sequences targeting accounts with large storage footprints) whose trie reads/writes accumulate recorded storage proof beyond the configured per-receipt (and consequently per-chunk) storage-witness size limit, without ever triggering a limit failure, because the only enforcement point is scoped to the VM's `FunctionCall` execution.

### Impact Explanation
Because the storage witness recorded for a chunk backs stateless validation, an attacker-controlled transaction that inflates the storage proof past the intended bound via non-`FunctionCall` actions can produce an oversized state witness that either: (a) fails to propagate/validate under stateless-validation size constraints, or (b) is silently under-bounded relative to the protocol's intended per-receipt cap, undermining the guarantee that `EnforcePerReceiptStorageProofLimit` is meant to provide. This maps to the "state-witness limits" category explicitly permitted in scope, and an oversized/undercapped witness can cause chunk production/validation failures — a transaction-triggered halt or invalid-state-transition risk at the chunk level, reachable purely by a normal transaction signer crafting a receipt with many non-`FunctionCall` actions.

### Likelihood Explanation
High likelihood of reachability: any account can submit a transaction whose action list is composed primarily of non-`FunctionCall` actions (e.g., repeated `AddKey`/`DeleteKey` against accounts with large numbers of existing keys, or `DeployContract`/account-deletion sequences against accounts with large storage usage) up to the existing `max_actions_per_receipt`/`TotalNumberOfActionsExceeded` bounds, without needing any privileged access, validator role, or network-level position. The bug requires no cooperation from other parties and is purely a data/structuring choice by the signer.

### Recommendation
Extend storage-proof-limit enforcement outside the VM so it applies uniformly to every action kind within a receipt, per the plan already described for `EnforceStorageProofLimitForAllActions`: after executing each action (not just `FunctionCall`), compare the receipt's recorded storage proof size against the configured limit and fail the receipt with `ActionErrorKind::ReceiptStorageProofSizeExceeded` once exceeded, rather than relying solely on the VM-internal `RecordedStorageCounter`.

### Proof of Concept
1. As an unprivileged signer, create/fund an account and populate it with a large number of access keys (via repeated `AddKey` transactions) so that trie operations against it (e.g., during `DeleteKey`, `DeployContract`, or `DeleteAccount`) touch a large storage footprint.
2. Submit a single receipt/transaction whose action list is composed of many such non-`FunctionCall` actions (e.g., a batch of `DeleteKey` actions against the large-storage account, or `DeployContract` followed by `DeleteAccount`), staying within `max_actions_per_receipt`.
3. Because `RecordedStorageCounter::observe_size` is only invoked from VM host functions during `FunctionCall` execution [1](#0-0) , none of the trie-proof growth from these actions is checked against `size_limit`, and the receipt completes without triggering `ReceiptStorageProofSizeExceeded`, even though the accumulated storage proof for the receipt exceeds the intended per-receipt cap documented for `EnforceStorageProofLimitForAllActions` [2](#0-1) .

### Citations

**File:** runtime/near-vm-runner/src/logic/recorded_storage_counter.rs (L17-33)
```rust
    /// Update the latest observed storage proof size and check if it exceeds the limit.
    /// Should be called after every trie operation.
    pub fn observe_size(&mut self, latest_storage_proof_size: usize) -> Result<(), VMLogicError> {
        self.last_observed_storage_size = latest_storage_proof_size;

        let current_size = self.get_storage_size()?;
        if current_size > self.size_limit {
            let limit_u64 = self.size_limit.try_into().map_err(|_| {
                VMLogicError::InconsistentStateError(InconsistentStateError::IntegerOverflow)
            })?;
            return Err(VMLogicError::HostError(HostError::RecordedStorageExceeded {
                limit: ByteSize::b(limit_u64),
            }));
        }

        Ok(())
    }
```

**File:** core/primitives-core/src/version.rs (L466-473)
```rust
    EnforcePerReceiptStorageProofLimit,
    /// Extend the per-receipt storage proof limit to every action kind. The
    /// `RecordedStorageCounter` only runs inside the VM, so it bounds
    /// `FunctionCall` actions alone; other actions in the same receipt could
    /// record proof past the limit. Check the receipt's recorded size after
    /// each action and fail the receipt with
    /// `ActionErrorKind::ReceiptStorageProofSizeExceeded` once it goes over.
    EnforceStorageProofLimitForAllActions,
```
