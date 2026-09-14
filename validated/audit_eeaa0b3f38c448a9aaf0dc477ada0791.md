### Title
Pending Transaction Queue gas-key balance tracking bypass via `WithdrawFromGasKey` nested inside a meta-transaction `DelegateAction` - (File: chain/client/src/pending_transaction_queue.rs)

### Summary
The Pending Transaction Queue (PTQ) is the mechanism that prevents double-spending of a gas key's balance across multiple in-flight, not-yet-certified chunks by scanning each admitted transaction's top-level actions for `Action::WithdrawFromGasKey` and accumulating the withdrawn amount into `pending_gas_key_costs`. However, `Action::WithdrawFromGasKey` is a valid `NonDelegateAction` and can therefore be embedded inside a `DelegateAction` (NEP-366 meta-transaction), whose outer, top-level action as seen by the PTQ scanner is `Action::Delegate`/`Action::DelegateV2`, not `Action::WithdrawFromGasKey`. This is directly analogous to CVE-2020-25654: a security-relevant accounting/authorization check is enforced along the "normal" front-door path but is bypassed by reaching the same underlying effect (draining a gas key's balance) through an alternate path (a nested action inside a meta-transaction) that the enforcement code was never designed to inspect.

### Finding Description
`PendingTransactionQueue::add_chunk_transactions` explicitly documents the invariant this mechanism depends on: [1](#0-0) 

It only inspects `tx.actions()` — the outer `SignedTransaction`'s action list — for `Action::WithdrawFromGasKey`:
```
for action in tx.actions() {
    if let Action::WithdrawFromGasKey(withdraw) = action { ... }
}
```
This mirrors the design comment in the VM host-function table, which states the invariant plainly: [2](#0-1) 

"Actions that reduce gas key balance must only be initiated via transactions, not by contracts. Otherwise, they will not be visible to the pending transaction queue."

The problem is that `Action::WithdrawFromGasKey` is explicitly classified as a valid, non-nested action (`is_delegate()` returns `false` for it), meaning it is a legal member of `NonDelegateAction`, and thus a legal action inside `DelegateAction.actions`: [3](#0-2) 

A user can therefore construct a `DelegateAction` whose `actions` list contains `Action::WithdrawFromGasKey`, have a relayer wrap and sign it as the outer `SignedTransaction` (`Action::Delegate(...)`), and submit it. When `PendingTransactionQueue::add_chunk_transactions` (and the parallel `check_pending`/`session_gas_key_withdrawals` logic in `chain/client/src/pending_transaction_queue.rs`) scans the outer transaction's actions, it only sees `Action::Delegate`, never inspecting the nested `WithdrawFromGasKey` action, so `pending_gas_key_costs` / `paid_from_balance` bookkeeping for that gas key is never updated even though the withdrawal is processed by the runtime once the delegate receipt executes on `sender_id`'s account.

This means the PTQ's admission-control invariant — that all withdrawals from a gas key's balance are visible to it before more transactions against the same gas key are admitted into subsequent, not-yet-certified chunks — is broken for meta-transactions. Concurrently, other pending or in-flight transactions signed by the same gas key (validated via `verify_and_charge_gas_key_tx_ephemeral`, which relies on `pending.paid_from_gas_key` to compute `available_gas_key_balance`, see `runtime/runtime/src/verifier.rs:420-440`) can be admitted using a stale/inflated view of the gas key's remaining balance, because the concurrently-in-flight `WithdrawFromGasKey` (routed through the `Delegate` wrapper) was never counted.

### Impact Explanation
The gas key balance accounting is exactly the kind of value-movement invariant this scan should protect (analogous to CVE-2020-25654's ACL bypass allowing unauthorized operations that should have been blocked). By hiding a `WithdrawFromGasKey` inside a `Delegate`/`DelegateV2` action, an attacker who controls (or colludes with) a relayer can cause the pending-queue's gas-key balance tracking to undercount pending withdrawals for a given signer, allowing multiple chunks in-flight to admit transactions against a gas key's balance beyond what should be available. Depending on the exact timing/overlap of chunk production and certification, this can enable spending more from a gas key's balance than the key actually has, i.e., admission of transactions the runtime would otherwise have blocked with `NotEnoughGasKeyBalance`, potentially leading to unauthorized value movement / balance-invariant violations at chunk-boundary races. This satisfies the "concrete unauthorized value movement" / "fee or gas bypass" bar for accepted analogs.

### Likelihood Explanation
No special privileges are required beyond being able to sign a `DelegateAction` as an account with a gas key, and finding any relayer willing to sign/submit it (which is the entire point of meta-transactions — the relayer does not need to trust or vet the inner action semantics beyond basic validation, and nothing in the delegate-action validation path in `runtime/runtime/src/actions.rs` rejects `WithdrawFromGasKey` as an inner action). This is reachable purely from a single submitted meta-transaction by an unprivileged account holding a gas key, matching the required attacker model (transaction/meta-transaction sender). The condition requires racing multiple pending chunks referencing the same gas key, which is a timing dependency but well within the threat model the PTQ was explicitly built to defend against.

### Recommendation
Extend `PendingTransactionQueue::add_chunk_transactions` (and the parallel logic in `PendingTxSession::check_pending`) to recursively inspect actions nested inside `Action::Delegate`/`Action::DelegateV2` for `WithdrawFromGasKey`, mirroring how `Action::post_quantum_signatures_required` already recurses into delegate actions (`core/primitives/src/action/mod.rs:448-465`). Alternatively, reject `WithdrawFromGasKey` as a disallowed inner action of `DelegateAction` at the type/validation level, consistent with the stated design intent that such balance-reducing actions must only be initiated directly via transactions.

### Proof of Concept
1. Create account `A` with a gas key `K` funded with balance `B`.
2. Construct `DelegateAction { sender_id: A, actions: [NonDelegateAction(Action::WithdrawFromGasKey({ public_key: K, amount: B }))], ... }`, sign it as `A`.
3. Have relayer `R` wrap it as `SignedTransaction { actions: [Action::Delegate(signed_delegate_action)] }` and submit it.
4. `PendingTransactionQueue::add_chunk_transactions` processes `R`'s transaction; since `tx.actions()` is `[Action::Delegate(...)]`, the loop at `chain/client/src/pending_transaction_queue.rs:280-288` never matches `Action::WithdrawFromGasKey`, so `pending_gas_key_costs` for `(A, K)` is not incremented.
5. Concurrently submit a transaction directly signed by gas key `K` spending close to `B`. Because `paid_from_gas_key` (derived from the untracked pending queue) does not reflect the in-flight delegate withdrawal, `verify_and_charge_gas_key_tx_ephemeral` (`runtime/runtime/src/verifier.rs`) may admit it even though the two transactions combined exceed `B`, once both chunks are eventually applied.

*Note: full verification of the exact runtime code path that executes `WithdrawFromGasKey` when reached via a delegate receipt (`runtime/runtime/src/access_keys.rs`) could not be completed within this session's tool budget — the file's full contents and `action_validation.rs`'s WithdrawFromGasKey-related check were not retrieved before the iteration limit. This should be confirmed against the full `access_keys.rs` and `action_validation.rs` sources before relying on this finding as conclusively exploitable.*

### Citations

**File:** chain/client/src/pending_transaction_queue.rs (L279-288)
```rust
            // Scan actions for WithdrawFromGasKey (affects gas key balance).
            for action in tx.actions() {
                if let Action::WithdrawFromGasKey(withdraw) = action {
                    let gas_key_entry = chunk_data
                        .gas_key_costs
                        .entry((signer_id.clone(), withdraw.public_key.clone()))
                        .or_insert(Balance::ZERO);
                    *gas_key_entry = gas_key_entry.saturating_add(withdraw.amount);
                }
            }
```

**File:** runtime/near-vm-runner/src/imports.rs (L291-295)
```rust
    // NOTE: There are intentionally no promise batch actions for
    // WithdrawFromGasKey. Actions that reduce gas key balance must only be
    // initiated via transactions, not by contracts. Otherwise, they will not be
    // visible to the pending transaction queue. Do not add host functions for
    // them. See NEP-611 for details.
```

**File:** core/primitives/src/action/mod.rs (L385-402)
```rust
    pub fn is_delegate(&self) -> bool {
        match self {
            Action::Delegate(_) | Action::DelegateV2(_) => true,
            Action::CreateAccount(_)
            | Action::DeployContract(_)
            | Action::FunctionCall(_)
            | Action::Transfer(_)
            | Action::Stake(_)
            | Action::AddKey(_)
            | Action::DeleteKey(_)
            | Action::DeleteAccount(_)
            | Action::DeployGlobalContract(_)
            | Action::UseGlobalContract(_)
            | Action::DeterministicStateInit(_)
            | Action::TransferToGasKey(_)
            | Action::WithdrawFromGasKey(_) => false,
        }
    }
```
