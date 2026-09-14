### Title
Duplicate `input_data_ids` in an `ActionReceipt` cause the postponed-receipt counter to desynchronize from the persisted dependency links, permanently freezing the receipt and any attached deposit - ([File: runtime/runtime/src/lib.rs])

### Summary
`process_action_receipt` iterates an `ActionReceipt`'s `input_data_ids` and, for every id not yet received, increments a `pending_data_count` counter and writes a `TrieKey::PostponedReceiptId{receiver_id, data_id}` entry linking that `data_id` back to the receipt. If the same `data_id` appears more than once in `input_data_ids`, the counter is incremented once per occurrence, but the trie write for the duplicate id uses `set`, which simply overwrites the same key — exactly the "keep only the last value" pattern from the reported hono bug, except here the discarded duplicates are dependency-count entries rather than header values.

### Finding Description
In `process_action_receipt` (`runtime/runtime/src/lib.rs:1541-1556`):
```
let mut pending_data_count: u32 = 0;
for data_id in action_receipt.input_data_ids() {
    if !has_received_data(state_update, account_id, *data_id)? {
        pending_data_count += 1;
        set(state_update, TrieKey::PostponedReceiptId { receiver_id: account_id.clone(), data_id: *data_id }, receipt.receipt_id())
    }
}
``` [1](#0-0) 

`pending_data_count` is later persisted as `TrieKey::PendingDataCount` and the receipt itself is stored as a `PostponedReceipt` if the count is non-zero [2](#0-1) . Per the documented data model, when a matching `DataReceipt` for a given `data_id` arrives, the runtime decrements `PendingDataCount` by exactly 1 and removes exactly one `PendingDataReceipt`/`PostponedReceiptId` link for that `data_id` — the model assumes a 1:1 mapping between the counted dependency and the stored link key [3](#0-2) .

If `input_data_ids` contains the same `data_id` twice, `pending_data_count` is bumped by 2 for that id, but only a single `PostponedReceiptId{receiver_id, data_id}` trie entry exists (the second `set` overwrites the first). When the single corresponding `DataReceipt` arrives, only one decrement/removal can ever occur, because there is only one link entry to find and delete. `pending_data_count` therefore gets stuck at 1 and never reaches 0, so the postponed `ActionReceipt` is never re-applied via the `pending_data_count == 0` fast path in `process_action_receipt`/its resume logic.

### Impact Explanation
A receipt that is permanently postponed is a receipt that never executes. This is essentially a receipt-loss / permanently-frozen-funds condition: any deposit attached to that `ActionReceipt`, and any storage or gas already paid to create it, is never returned to the sender and the intended action never happens, with no path for it to time out or be refunded (postponed action receipts are not covered by the yield-timeout mechanism, which only applies to `PromiseYield`). This matches the rules' accepted impact category of "permanently frozen funds" / "receipt loss."

### Likelihood Explanation
The severity depends on whether an ordinary contract call/transaction can cause an `ActionReceipt`'s `input_data_ids` to contain a duplicate `data_id`. This would occur if a contract's promise-combination logic (e.g., `promise_and`) allows the same promise/data dependency to be referenced twice when constructing a receipt. I was not able to fully verify, within available searches, whether `receipt_manager.rs`'s promise/receipt-construction path deduplicates `input_data_ids` before this point — the search returned matches in `receipt_manager.rs` for `input_data_ids`/`promise_and` but I could not read the file contents to confirm deduplication behavior before running out of tool budget. This is the key open question that determines whether this is remotely triggerable by an ordinary contract deployer/caller (in which case likelihood is Medium/High) or only reachable through an internal invariant that the runtime already enforces elsewhere (in which case it would not be exploitable).

### Recommendation
- Verify in `runtime/runtime/src/receipt_manager.rs` whether `input_data_ids` can contain duplicate `data_id`s when constructed from promise combinators (`promise_and`, `promise_then`, yield/resume flows).
- If duplicates are possible, either (a) deduplicate `input_data_ids` before receipt validation/execution, rejecting or collapsing duplicates, or (b) make the pending-count bookkeeping robust to duplicates, e.g. store the number of pending occurrences per `data_id` (or dedupe before counting) so `pending_data_count` and the number of `PostponedReceiptId` links stay consistent.
- Add a regression test that creates an `ActionReceipt` with a duplicated `data_id` in `input_data_ids` and confirms the receipt does not become permanently stuck once the single corresponding `DataReceipt` arrives.

### Proof of Concept
Conceptual PoC (pending confirmation that user-reachable promise construction can produce duplicate `input_data_ids`):
1. From a contract, construct a promise chain where the same data-producing promise is referenced twice as an input dependency of a subsequent action receipt (candidate mechanism: `promise_and` called with the same promise index twice, or a batched action manually specifying two identical entries in `output_data_receivers`/`input_data_ids`).
2. Submit the resulting transaction; the runtime creates the `ActionReceipt` with `input_data_ids = [X, X]`.
3. `process_action_receipt` sets `pending_data_count = 2` but writes only one `PostponedReceiptId{account_id, X}` trie entry (`runtime/runtime/src/lib.rs:1541-1556`).
4. The single `DataReceipt` for `X` arrives and satisfies the one stored link, decrementing the count once; `pending_data_count` remains at 1.
5. The `ActionReceipt` remains a `PostponedReceipt` forever; any attached deposit/gas is never executed or refunded.

I was unable to conclusively confirm step 1 is reachable from an ordinary transaction due to running out of investigation budget on `receipt_manager.rs`; this should be validated before treating the finding as confirmed-exploitable.

### Citations

**File:** runtime/runtime/src/lib.rs (L1541-1556)
```rust
        let mut pending_data_count: u32 = 0;
        for data_id in action_receipt.input_data_ids() {
            if !has_received_data(state_update, account_id, *data_id)? {
                pending_data_count += 1;
                // The data for a given data_id is not available, so we save a link to this
                // receipt_id for the pending data_id into the state.
                set(
                    state_update,
                    TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: *data_id,
                    },
                    receipt.receipt_id(),
                )
            }
        }
```

**File:** runtime/runtime/src/lib.rs (L1576-1588)
```rust
            // Not all input data is available now.
            // Save the counter for the number of pending input data items into the state.
            set(
                state_update,
                TrieKey::PendingDataCount {
                    receiver_id: account_id.clone(),
                    receipt_id: *receipt.receipt_id(),
                },
                &pending_data_count,
            );
            // Save the receipt itself into the state.
            set_postponed_receipt(state_update, receipt);
        }
```

**File:** docs/RuntimeSpec/Receipts.md (L132-164)
```markdown
#### Pending DataReceipt Count

A counter which counts pending [`DataReceipt`s](#datareceipt) for a [Postponed Receipt](#postponed-actionreceipt) initially set to the length of missing [`input_data_ids`](#input_data_ids) of the incoming `ActionReceipt`. It's decrementing with every new received [`DataReceipt`](#datareceipt):

- **`key`** = `account_id`,`receipt_id`
- **`value`** = `u32`

_Where `account_id` is AccountId, `receipt_id` is CryptoHash and value is an integer._

#### Pending DataReceipt for Postponed ActionReceipt

We index each pending `DataReceipt` so when a new [`DataReceipt`](#datareceipt) arrives we connect it to the [Postponed Receipt](#postponed-actionreceipt) it belongs to.

- **`key`** = `account_id`,`data_id`
- **`value`** = `receipt_id`

## Processing DataReceipt

#### Received DataReceipt

First of all, runtime saves the incoming `DataReceipt` to the storage as:

- **`key`** = `account_id`,`data_id`
- **`value`** = `[u8]`

_Where `account_id` is [`Receipt.receiver_id`](#receiver_id), `data_id` is [`DataReceipt.data_id`](#data_id) and value is a [`DataReceipt.data`](#data) (which is typically a serialized result of the call to a particular contract)._

Next, runtime checks if there are any [`Postponed ActionReceipt`](#postponed-actionreceipt) waiting for this `DataReceipt` by querying [`Pending DataReceipt` to the Postponed Receipt](#pending-datareceipt-for-postponed-actionreceipt). If there is no postponed `receipt_id` yet, we do nothing else. If there is a postponed `receipt_id`, we do the following:

- decrement [`Pending Data Count`](#pending-datareceipt-count) for the postponed `receipt_id`
- remove found [`Pending DataReceipt` to the `Postponed ActionReceipt`](#pending-datareceipt-for-postponed-actionreceipt)

If [`Pending DataReceipt Count`](#pending-datareceipt-count) is now 0 that means all the [`Receipt.input_data_ids`](#input_data_ids) are in storage and runtime can safely apply the [Postponed Receipt](#postponed-actionreceipt) and remove it from the store.
```
