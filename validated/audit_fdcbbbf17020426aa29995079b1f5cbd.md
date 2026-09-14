### Title
Unhandled `RuntimeError::UnexpectedIntegerOverflow` / `ReceiptValidationError` in `apply_chunk` panics the whole chunk instead of failing only the offending transaction/receipt - (File: `chain/chain/src/runtime/mod.rs`)

### Summary
The external report's root cause is that a missing validation/limit check in *one* sub-operation (a derivative deposit) is allowed to abort the *entire* batched operation (`stake()`), denying service to unrelated, independent work that should have succeeded on its own. The closest reachable analog in nearcore is the opposite-but-equivalent failure mode in the runtime apply path: most per-receipt/per-action failures are correctly isolated (rolled back per receipt, chunk still succeeds), but two specific `RuntimeError` variants returned from `Runtime::apply` are explicitly *not* handled gracefully and instead `panic!` the whole chunk-apply call, turning what should be an isolated, per-transaction failure into a node crash / chunk-level halt.

### Finding Description
`Runtime::apply` documents that invalid/failing transactions and receipts are supposed to be skipped or rolled back individually so "the protocol can make progress" [1](#0-0) . Failing actions inside a receipt are isolated: the action loop breaks on the first error, records the action index, and the receipt is rolled back without touching other receipts [2](#0-1) , and this isolation is explicitly tested (a receipt failing on `TotalPromiseInputSizeExceeded` does not affect sibling receipts in the same chunk) [3](#0-2) .

However, the caller of `Runtime::apply`, `process_state_update` in `chain/chain/src/runtime/mod.rs`, maps two `RuntimeError` variants to a hard `panic!` rather than a graceful per-tx/per-receipt failure, with an explicit `// TODO(#2152): process gracefully` marker: [4](#0-3) 

This means that if a single transaction or receipt is able to drive an arithmetic overflow into a `checked_add_result`/`IntegerOverflowError` path that surfaces as `RuntimeError::UnexpectedIntegerOverflow` (rather than being converted into a per-outcome `ActionErrorKind`/`InvalidTxError`), or if a receipt fails `validate_receipt` at a point that returns `RuntimeError::ReceiptValidationError` instead of a per-receipt `ActionError`, the entire `apply_chunk` call for that shard panics. `apply_chunk` in turn wraps `process_state_update` errors and re-panics for anything other than specific storage errors [5](#0-4) . A chunk contains many independent transactions/receipts from many unrelated signers (analogous to SafEth's independent per-derivative deposits); one bad transaction reaching this path halts processing for the whole chunk/shard rather than failing only itself — the same class of bug as the external report ("missing isolation on one sub-operation kills unrelated ones"), except here the blast radius is a validator process crash / chunk-apply halt rather than just an EVM-level revert.

The runtime's own documentation confirms the intended design contract that "the only alternative way to handle these transactions is to make the entire chunk invalid" is supposed to be avoided by skipping bad txs, not panicking [1](#0-0) , and the `merge`/`set_error` isolation machinery exists specifically to keep failures receipt-scoped [6](#0-5) . The two `panic!` arms are a deliberately-marked-incomplete exception to that contract.

### Impact Explanation
If reachable via a single crafted transaction/receipt (a signer-controlled input), this becomes a transaction-triggered halt: the validator/RPC node applying that chunk panics, and depending on process-supervision behavior this can repeatedly crash the node reprocessing the same chunk, producing a chain-wide liveness failure across all honest nodes that must apply that chunk (since state transition is deterministic, all validators tracking the shard hit the same panic). This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Likelihood is **uncertain based on available evidence**. I could not, within the given search budget, confirm a concrete reachable path from an unprivileged transaction to `RuntimeError::UnexpectedIntegerOverflow` or `RuntimeError::ReceiptValidationError` being returned from `Runtime::apply` (as opposed to being converted into a per-outcome `ActionError`/`InvalidTxError` earlier in the pipeline, which is the normal/safe path). The code explicitly marks these as `TODO(#2152): process gracefully`, indicating the nearcore team is already aware these paths are not gracefully handled, but whether current overflow/receipt-validation checks upstream (`checked_add_result`, `validate_receipt`, action validation limits) fully prevent an attacker from ever triggering these specific `RuntimeError` variants is not something I could verify with certainty from the index alone.

### Recommendation
- Audit all call sites that can produce `RuntimeError::UnexpectedIntegerOverflow` or `RuntimeError::ReceiptValidationError` from `Runtime::apply` and confirm none of them can be triggered by attacker-controlled transaction/receipt contents (only by protocol bugs/internal invariants).
- Resolve the `TODO(#2152)` by converting any transaction/receipt-triggerable overflow or receipt-validation failure into a per-outcome failed execution result (as is already done for the vast majority of action/tx failures), rather than a `panic!`, so a single malformed transaction cannot halt an otherwise-healthy chunk.
- Add fuzz/property tests analogous to `test_promise_input_size_limit_does_not_affect_other_receipts` [3](#0-2)  specifically targeting the overflow and receipt-validation-error boundary to prove isolation holds even for these two variants.

### Proof of Concept
Not independently reproducible from the indexed code alone — a full PoC would require identifying an accepted transaction shape that causes `checked_add_result`/`IntegerOverflowError` to bubble up as `RuntimeError::UnexpectedIntegerOverflow` (or a receipt that fails `validate_receipt` as `RuntimeError::ReceiptValidationError`) at the `Runtime::apply` return boundary, rather than being caught and converted into a per-action `ActionError` earlier. The structural evidence for the bug class is the explicit `panic!` handling with the `TODO(#2152): process gracefully` comment: [4](#0-3) . Given the index size limits, some call sites for these error variants may not be fully visible; a Devin session with full repo access would be needed to trace every producer of these two `RuntimeError` variants and confirm/deny attacker reachability.

### Citations

**File:** runtime/runtime/src/lib.rs (L530-541)
```rust
    /// Marks the receipt as failed: records the error and discards any
    /// receipt-scoped state that would otherwise leak across the failure
    /// boundary (queued receipts, proposed validators, burnt/subsidized
    /// balances). Profile, gas counters, logs and `current_contracts` are
    /// kept — they reflect work already done.
    pub fn set_error(&mut self, err: ActionError) {
        self.result = Err(err);
        self.new_receipts.clear();
        self.validator_proposals.clear();
        self.tokens_burnt = Balance::ZERO;
        self.subsidized_amount = Balance::ZERO;
    }
```

**File:** runtime/runtime/src/lib.rs (L1000-1004)
```rust
                // TODO storage error
                if let Err(ref mut res) = result.result {
                    res.index = Some(action_index as u64);
                    break;
                }
```

**File:** runtime/runtime/src/lib.rs (L1849-1852)
```rust
    /// Invalid transactions should have been filtered out by the chunk producer, but if a chunk
    /// containing invalid transactions does make it to here, these transactions are skipped. This
    /// does pollute the chain with junk data, but it also allows the protocol to make progress, as
    /// the only alternative way to handle these transactions is to make the entire chunk invalid.
```

**File:** runtime/runtime/src/tests/apply.rs (L5199-5202)
```rust
/// Failing one receipt for exceeding the promise-input size limit must not
/// affect other receipts processed in the same chunk, and `apply` must succeed
/// (a per-receipt failure, not a chunk-level error).
#[test]
```

**File:** chain/chain/src/runtime/mod.rs (L361-374)
```rust
            .map_err(|e| match e {
                RuntimeError::InvalidTxError(err) => {
                    tracing::warn!(?err, "invalid tx");
                    Error::InvalidTransactions
                }
                // TODO(#2152): process gracefully
                RuntimeError::UnexpectedIntegerOverflow(reason) => {
                    panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
                }
                RuntimeError::StorageError(e) => Error::StorageError(e),
                // TODO(#2152): process gracefully
                RuntimeError::ReceiptValidationError(e) => panic!("{}", e),
                RuntimeError::ValidatorError(e) => e.into(),
            })?;
```

**File:** chain/chain/src/runtime/mod.rs (L1296-1304)
```rust
            Err(e) => match e {
                Error::StorageError(err) => match &err {
                    StorageError::FlatStorageBlockNotSupported(_)
                    | StorageError::MissingTrieValue(..) => Err(err.into()),
                    _ => panic!("{err}"),
                },
                _ => Err(e),
            },
        }
```
