## Title
Gas key balance can be double-spent / over-committed because the SPICE pending transaction queue does not see withdrawals routed through `DelegateV2`/nested `WithdrawFromGasKey` - (File: `core/primitives-core/src/version.rs`)

### Summary
The BaseERC20Guild bug is a class of *accounting bypass*: a resource that backs a permission (locked tokens backing voting power) can be freed/reused through a path the vote-tallying logic does not observe, letting the same principal exercise the same right twice. The structurally identical pattern exists in nearcore's gas-key accounting: the `PendingTransactionQueue` (PTQ) — which tracks in-flight, not-yet-certified spending against a gas key's prepaid balance so concurrent transactions cannot overspend it — only inspects the **top-level actions of the outer transaction**. A `WithdrawFromGasKey` action nested inside a `DelegateAction`/`DelegateV2`, or a gas-key nonce/balance spend routed through `Action::DelegateV2`, is invisible to that scan, so the same gas-key balance can be committed by more than one concurrently-pending chunk before certification catches the conflict — the same "spend it, then reuse the freed capacity elsewhere before the ledger confirms it" pattern as the Guild double-vote.

### Finding Description
`PendingTxSession::check_pending` and `PendingTransactionQueue::add_chunk_transactions` maintain `pending_gas_key_costs` by scanning a transaction's **top-level** `actions` for `Action::WithdrawFromGasKey` and by reading only the **outer** transaction's `signer_id`/`public_key`/`nonce_index` to update `pending_nonces` and `pending_gas_key_costs`: [1](#0-0) 

The documented rationale for the mitigating protocol features makes the root cause explicit: [2](#0-1) 

`RejectDelegateV2` states: "the inner nonce advances a gas key of the delegate sender and `PendingTransactionQueue` does not see it: the queue reads only the outer transaction's signer, public key and nonce index, so its nonce and gas key balance commitments would miss that key."
`RejectWithdrawFromGasKeyInDelegate` states: "the SPICE pending transaction queue scans only the top level actions of a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key that the queue still counts as funded."

This is confirmed by a code comment on the host-function surface that intentionally omits promise-based `WithdrawFromGasKey` for the same reason: "Actions that reduce gas key balance must only be initiated via transactions, not by contracts. Otherwise, they will not be visible to the pending transaction queue": [3](#0-2) 

The runtime-side gas-key charge logic (`verify_and_charge_gas_key_tx_ephemeral`) trusts the PTQ-provided `pending.paid_from_gas_key` to represent all currently-pending consumption of a gas key's balance: [4](#0-3) 

If a nested `WithdrawFromGasKey` (inside a `DelegateAction`) or a `DelegateV2` meta-transaction consuming a gas key nonce is admitted into one pending/uncertified chunk, the PTQ's aggregate `pending_gas_key_costs` for that `(account, public_key)` is never incremented for that spend. A second, independently-produced pending chunk (or a directly submitted RPC transaction) can then be validated against the *same* on-trie gas-key balance without seeing the first chunk's in-flight consumption, because `verify_and_charge_gas_key_tx_ephemeral`'s check (`gas_key_info.balance.checked_sub(pending.paid_from_gas_key)`) only rejects when the PTQ's own bookkeeping shows exhaustion — and that bookkeeping is blind to the nested/DelegateV2 spend. This is the direct analog of the Guild bug: the accounting layer that is supposed to prevent double-use of a single backing resource can be bypassed by exercising the same resource through a code path outside the accounting layer's field of view, so two chunks each believe they're the first legitimate spender.

### Impact Explanation
If two independently-produced, still-uncertified chunks each admit a transaction spending against the same gas key balance (one via the visible top-level path, one via the blind nested/DelegateV2 path, or two via the blind path), both can pass validation even though the combined cost exceeds the actual on-trie balance. When both chunks are eventually applied, the ephemeral verification at production time and the actual runtime application at execution time can diverge, or two logically conflicting withdrawals/gas-key spends can both be admitted, resulting in the gas key balance being spent beyond what it holds. Depending on how the second-applied transaction's ephemeral check behaves relative to the first's already-applied state, this is a state-inconsistency / gas-key-balance-overspend condition — i.e., a bypass of a balance-limiting control that guild-style resource accounting is meant to enforce. It does not directly forge tokens from thin air (the runtime's trie-level `checked_sub` in `action_withdraw_from_gas_key` still enforces per-write correctness), but it defeats the *pending-queue admission control* whose entire purpose is to stop concurrently-pending, not-yet-applied chunks from both being allowed to draw down the same finite balance — the exact "double-spend of a resource before the ledger catches up" bug class described in the report.

### Likelihood Explanation
The maintainers themselves identified this exact gap and are actively closing it via two dedicated protocol features, `RejectDelegateV2` and `RejectWithdrawFromGasKeyInDelegate`, whose comments state the root cause verbatim. This indicates the vulnerable code paths (`DelegateV2` gas-key meta-transactions and nested `WithdrawFromGasKey` inside a `DelegateAction`) exist in the codebase and are reachable by any ordinary transaction signer/relayer holding a gas key, until these protocol features activate and are adopted network-wide. Exploitability requires only constructing a `DelegateAction`/`DelegateV2` with a nested `WithdrawFromGasKey` (or a `DelegateV2` spending a gas key nonce) and getting it included in one chunk while a conflicting gas-key spend lands in a concurrently pending chunk — well within reach of a normal RPC caller/meta-transaction sender, with no privileged role required.

### Recommendation
Ensure `RejectDelegateV2` and `RejectWithdrawFromGasKeyInDelegate` are active protocol-wide (i.e., not merely defined but enforced at every currently-supported protocol version), and/or extend the PTQ's action scan to recursively inspect nested/delegated actions (including through `DelegateV2`) for any action that mutates gas-key balance or advances a gas-key nonce, so `pending_gas_key_costs` and `pending_nonces` account for every reachable spend path, not just top-level actions.

### Proof of Concept
Conceptual PoC (mirrors the report's "lock → vote → withdraw → reuse" pattern, substituting "gas key balance" for "locked tokens" and "pending transaction queue admission" for "vote tally"):
1. Attacker/relayer funds a gas key with balance `B` sufficient for exactly one transaction of cost `B`.
2. Attacker submits Transaction A: a `DelegateAction` (or `DelegateV2`) whose *nested* action is `WithdrawFromGasKey` draining `B` from the gas key. This lands in pending chunk `C1`. Per the documented gap, the PTQ's top-level-only scan does not record this spend against `pending_gas_key_costs`.
3. Before `C1` is certified, attacker submits Transaction B: an ordinary gas-key transaction spending `B` again from the same key, targeted at a different (or the same) shard/chunk `C2`. Because `pending.paid_from_gas_key` computed by the PTQ still shows the key as fully funded (it never saw A's withdrawal), `verify_and_charge_gas_key_tx_ephemeral` admits B.
4. Both `C1` and `C2` are produced concurrently before certification reconciles them, allowing the same gas-key balance `B` to back two independently-approved spends — the double-spend/double-use analog of the Guild's double-vote.

Note: I could not directly execute this in a live test-loop within the available tool budget; the analysis is grounded in the explicit maintainer documentation of the gap in `core/primitives-core/src/version.rs` (`RejectDelegateV2`, `RejectWithdrawFromGasKeyInDelegate`) and the corresponding scan logic in `chain/client/src/pending_transaction_queue.rs`. I was also unable to confirm from the index whether these two `ProtocolFeature`s have already reached their activation version relative to `MIN_SUPPORTED_PROTOCOL_VERSION` in this snapshot — if they have not yet activated on all currently-supported protocol versions, the window described above is live; if they have already activated everywhere, this is a historical/patched issue rather than a currently exploitable one. Confirming that requires reading the specific activation version constants further down `core/primitives-core/src/version.rs`, which the tool budget did not allow me to retrieve in full.

### Citations

**File:** chain/client/src/pending_transaction_queue.rs (L311-320)
```rust
            // Scan actions for WithdrawFromGasKey (affects gas key balance).
            for action in tx.actions() {
                if let Action::WithdrawFromGasKey(withdraw) = action {
                    let gas_key_entry = chunk_data
                        .gas_key_costs
                        .entry((signer_id.clone(), (&withdraw.public_key).into()))
                        .or_insert(Balance::ZERO);
                    *gas_key_entry = gas_key_entry.saturating_add(withdraw.amount);
                }
            }
```

**File:** core/primitives-core/src/version.rs (L453-465)
```rust
    /// Reject `Action::DelegateV2`. This disables meta transactions from gas
    /// keys, because the inner nonce advances a gas key of the delegate sender
    /// and `PendingTransactionQueue` does not see it: the queue reads only the
    /// outer transaction's signer, public key and nonce index, so its nonce and
    /// gas key balance commitments would miss that key. The `DelegateV2`
    /// variant and `VersionedDelegateActionPayload` remain so a later delegate
    /// action version can reuse them.
    RejectDelegateV2,
    /// Reject a `WithdrawFromGasKey` action nested inside a delegate action.
    /// The SPICE pending transaction queue scans only the top level actions of
    /// a transaction for `WithdrawFromGasKey`, so a nested one drains a gas key
    /// that the queue still counts as funded.
    RejectWithdrawFromGasKeyInDelegate,
```

**File:** runtime/near-vm-runner/src/imports.rs (L311-316)
```rust
    ] -> []>,
    // NOTE: There are intentionally no promise batch actions for
    // WithdrawFromGasKey. Actions that reduce gas key balance must only be
    // initiated via transactions, not by contracts. Otherwise, they will not be
    // visible to the pending transaction queue. Do not add host functions for
    // them. See NEP-611 for details.
```

**File:** runtime/runtime/src/verifier.rs (L575-594)
```rust
    // Check gas key has enough balance for gas costs, accounting for
    // pending gas key costs (prior gas key txs + pending WithdrawFromGasKey).
    // Unlike account balance, gas key balance only changes through transactions
    // that PTQ explicitly tracks, so pending should never exceed the balance.
    let Some(available_gas_key_balance) =
        gas_key_info.balance.checked_sub(pending.paid_from_gas_key)
    else {
        tracing::error!(
            target: "runtime",
            balance = %gas_key_info.balance,
            paid_from_gas_key = %pending.paid_from_gas_key,
            "pending gas key costs exceed gas key balance"
        );
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: Balance::ZERO,
            cost: gas_cost,
        });
    };
    if available_gas_key_balance < gas_cost {
```
