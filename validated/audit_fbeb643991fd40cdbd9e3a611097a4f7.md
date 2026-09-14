### Title
Meta-transaction (NEP-366) relayer's identity is exposed via `signer_account_id()` to an attacker-chosen receiver contract, enabling unauthorized use of the relayer's on-chain identity - ([File: runtime/runtime/src/actions.rs])

### Summary
This is the NEAR analog of the "Relayer could lose funds via `tx.origin`-style checks in an unknown receiver" pattern from the Connext audit. In NEAR meta transactions, the relayer submits a `SignedDelegateAction` on behalf of a sender, but the resulting `ActionReceipt`'s `signer_id` field is set to the *relayer*, not the actual delegate sender, and this value is exposed to the receiver contract through the `signer_account_id()` host function — exactly analogous to `tx.origin`.

### Finding Description
When a relayer wraps and submits a `DelegateAction`, `apply_delegate_action` builds a brand-new receipt whose `ActionReceipt.signer_id` is copied from the outer receipt's signer (the relayer), while `predecessor_id` is set to the delegate sender: [1](#0-0) 

The sender fully controls `delegate_action.receiver_id` and the inner `actions` (including a `FunctionCall` to an arbitrary contract), while the relayer only controls which `DelegateAction` it chooses to wrap and pay for: [2](#0-1) 

The comment in the code itself acknowledges that the relayer's committed value can be affected by the sender's chosen target contract, but only discusses deposit refunds, not identity-based authorization: [3](#0-2) 

Crucially, on the receiver side, the contract executed by the delegated `FunctionCall` action can call `signer_account_id()`, which returns the *relayer's* account id (not the sender's), because `VMContext.signer_account_id` is populated straight from the receipt's `signer_id`: [4](#0-3) [5](#0-4) 

This is the exact `tx.origin`-analog described in the audit finding: a "receiver-side" contract chosen by an untrusted third party (here, the delegate sender) can branch on `signer_account_id()` believing it identifies the entity that authorized/paid for the call — but that identity is actually the relayer, who had no say in which receiver/method was invoked beyond agreeing to relay the wrapping transaction. If any contract on the network keys balances, allowances, or privileged actions off `signer_account_id()` (treating it as an authenticated "true caller" akin to `tx.origin` in the audited system), an attacker can craft a `DelegateAction` targeting that contract so that, once a relayer relays it, the call appears to originate from the relayer's identity, letting the sender trigger relayer-authorized behavior (e.g., withdrawing/spending funds or exercising permissions tied to the relayer's account) that the relayer never intended for that specific interaction.

The docs for meta-transactions confirm this identity substitution is a known but under-documented side effect, previously flagged only for balance refunds, not for `signer_account_id()`-based authorization patterns: [6](#0-5) [7](#0-6) 

### Impact Explanation
A relayer that provides a general-purpose meta-transaction relaying service is exposed to unauthorized use of its on-chain identity by any sender who crafts a `DelegateAction` targeting a receiver contract that trusts `signer_account_id()` for authorization or accounting. This can result in unauthorized value movement or privilege exercise tied to the relayer's account — the same class of loss described in the referenced report, adapted to NEAR's protocol-level identity fields rather than EVM's `tx.origin`. Because the relayer's account is potentially reused across many different unrelated relayed calls, the blast radius (funds, allowances, or permissions associated with the relayer account) can be significant if the relayer account is not single-purpose.

### Likelihood Explanation
Likelihood depends on ecosystem-contract behavior, not the protocol itself: it requires a receiving contract that authorizes actions or manages balances based on `signer_account_id()` in a `tx.origin`-like way, and a relayer that reuses a wallet holding value/permissions instead of a disposable relaying-only key. Since meta transactions are explicitly designed to let a sender target *any* receiver/method (`docs/architecture/how/meta-tx.md`), and relayers are documented as an application-layer, off-chain concept with minimal enforced protocol-level restriction on receiver identity, this is readily reachable by any unprivileged transaction sender who finds a relayer willing to relay to an attacker-chosen contract.

### Recommendation
Document explicitly (as with the original audit's "Connext: to be documented" resolution) that relayers must use single-purpose, minimally-funded/minimally-privileged accounts, since `signer_account_id()` exposed to arbitrary receiver contracts via delegated actions is not a safe authentication signal from the relayer's perspective. Consider also documenting for contract developers that `signer_account_id()` should not be treated as a strong authorization signal analogous to `tx.origin`, given meta-transactions make it attacker-influenced in this way.

### Proof of Concept
1. Sender Alice signs a `DelegateAction` with `receiver_id = "victim.near"` and `actions = [FunctionCall { method_name: "withdraw", ... }]`, where `victim.near` is a contract that authorizes `withdraw` calls by checking `signer_account_id()` against a recorded depositor account (mirroring the `tx.origin` check in the audited `xReceive` scenario).
2. Alice knows/expects that `Relayer-R`'s account holds a balance/permission on `victim.near` (e.g., because `Relayer-R` previously interacted with `victim.near` directly, or because `victim.near`'s authorization model trusts any account that appears as `signer_account_id()`).
3. Alice submits the `SignedDelegateAction` to `Relayer-R`'s off-chain endpoint; `Relayer-R` wraps it in a `SignedTransaction` and submits it, per the standard meta-tx flow in `apply_delegate_action`.
4. The receipt executed on `victim.near` carries `signer_id = Relayer-R`'s account (copied per `runtime/runtime/src/actions.rs:506`), so `signer_account_id()` inside `victim.near`'s `withdraw` method returns `Relayer-R`, causing the withdrawal/authorization logic to succeed using `Relayer-R`'s identity — without `Relayer-R` having chosen or reviewed that specific `withdraw` call.
5. Funds/permissions tied to `Relayer-R`'s account on `victim.near` are moved/exercised without `Relayer-R`'s specific consent for that action.

### Citations

**File:** runtime/runtime/src/actions.rs (L453-460)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
```

**File:** runtime/runtime/src/actions.rs (L499-513)
```rust
    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L515-519)
```rust
    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
```

**File:** runtime/near-vm-runner/src/logic/context.rs (L12-24)
```rust
    /// The account id of the current contract that we are executing.
    pub current_account_id: AccountId,
    /// The account id of that signed the original transaction that led to this
    /// execution.
    pub signer_account_id: AccountId,
    /// The public key that was used to sign the original transaction that led to
    /// this execution.
    pub signer_account_pk: PublicKey,
    /// If this execution is the result of cross-contract call or a callback then
    /// predecessor is the account that called it.
    /// If this execution is the result of direct execution of transaction then it
    /// is equal to `signer_account_id`.
    pub predecessor_account_id: AccountId,
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L591-618)
```rust
/// All contract calls are a result of some transaction that was signed by some account using
/// some access key and submitted into a memory pool (either through the wallet using RPC or by
/// a node itself). This function returns the id of that account. Saves the bytes of the signer
/// account id into the register.
///
/// # Errors
///
/// * If the registers exceed the memory limit returns `MemoryAccessViolation`.
/// * If called as view function returns `ProhibitedInView`.
///
/// # Cost
///
/// `base + write_register_base + write_register_byte * num_bytes`
pub fn signer_account_id(ctx: &mut Ctx, _memory: &mut [u8], register_id: u64) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;

    if ctx.context.is_view() {
        return Err(
            HostError::ProhibitedInView { method_name: "signer_account_id".to_string() }.into()
        );
    }
    ctx.registers.set(
        &mut ctx.result_state.gas_counter,
        &ctx.config.limit_config,
        register_id,
        ctx.context.signer_account_id.as_bytes(),
    )
}
```

**File:** docs/architecture/how/meta-tx.md (L225-242)
```markdown
## Balance refunds in meta transactions

Unlike gas refunds, the protocol sends balance refunds to the predecessor
(a.k.a. sender) of the receipt. This makes sense, as we deposit the attached
balance to the receiver, who has to explicitly reattach a new balance to new
receipts they might spawn.

In the world of meta transactions, this assumption is also challenged. If an
inner action requires an attached balance (for example a transfer action) then
this balance is taken from the relayer.

The relayer can see what the cost will be before submitting the meta transaction
and agrees to pay for it, so nothing wrong so far. But what if the transaction
fails execution on Bob's shard? At this point, the predecessor is `Alice` and
therefore she receives the token balance refunded, not the relayer. This is
something relayer implementations must be aware of since there is a financial
incentive for Alice to submit meta transactions that have high balances attached
but will fail on Bob's shard.
```

**File:** docs/RuntimeSpec/Components/BindingsSpec/ContextAPI.md (L34-55)
```markdown
#### signer_account_id

```rust
signer_account_id(register_id: u64)
```

All contract calls are a result of some transaction that was signed by some account using
some access key and submitted into a memory pool (either through the wallet using RPC or by a node itself). This function returns the id of that account.

###### Normal operation

- Saves the bytes of the signer account id into the register.

###### Panics

- If the registers exceed the memory limit panics with `MemoryAccessViolation`;
- If called in a view function panics with `ProhibitedInView`.

###### Current bugs

- Currently we conflate `originator_id` and `sender_id` in our code base.

```
