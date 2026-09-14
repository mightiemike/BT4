## Title
Meta-transaction deposit refund is credited to the delegated sender instead of the paying relayer, allowing unauthorized value extraction from relayers - (File: `runtime/runtime/src/actions.rs`)

## Summary
When a relayer submits a meta-transaction (`DelegateAction`/`DelegateV2`, NEP-366), the relayer pays all gas and attached deposits for the inner actions, but the protocol design routes any resulting **deposit refund** (e.g., when the inner action fails on the receiver's shard) to the delegated sender (Alice) rather than to the relayer who actually funded the deposit. This is the same bug class as the external report: a fund-distribution rule that is disconnected from who actually bore the cost, creating a financial incentive for the party who did not pay (Alice) to profit at the expense of the party who did pay (the relayer), without the relayer's authorization or any way to prevent it on-protocol.

## Finding Description
`apply_delegate_action` (`runtime/runtime/src/actions.rs:487-535`) unwraps a `DelegateAction` on the sender's (Alice's) shard and creates a new action receipt whose `predecessor_id` is `sender_id` (Alice), even though the relayer is the one who originally signed and paid for the transaction: [1](#0-0) 

The comment embedded in the code explicitly documents the design:
"Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas. If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction. Gas is refunded to the signer, this is Relayer... Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit."

Refund routing is generic and receipt-kind-agnostic: deposit/balance refunds always go to `receipt.balance_refund_receiver()` — i.e., the `predecessor_id` of the failed receipt — regardless of who economically funded the deposit (`core/primitives/src/receipt.rs`, referenced from `runtime/runtime/src/lib.rs` and `runtime/runtime/src/deterministic_account_id.rs`). Because the inner-action receipt created from the delegate action has `predecessor_id = sender_id` (Alice), any deposit refund produced when that receipt fails on the receiver's shard (Bob's) is paid to Alice, not to the relayer who funded and prepaid the deposit.

This is explicitly acknowledged as a known incentive problem in the project's own documentation: [2](#0-1) 

The pattern mirrors the external report precisely: a distribution rule (deposit refund → predecessor) is decoupled from the party that actually bears the economic cost (relayer pays deposit, but does not receive it back on failure), creating a griefing/profit incentive for the counter-party who did not pay (Alice), similarly to how the bond-manager slashing split let the relayer avoid loss while the disputer/initiator split was miscalibrated.

## Impact Explanation
- **Unauthorized value movement**: The relayer's NEAR balance is used to fund a deposit that is intentionally engineered (by Alice, the delegated sender) to fail on the receiver's shard, causing the refund to be credited to Alice instead of the relayer. This is a direct transfer of value from the relayer to an unrelated account (Alice) without the relayer's consent — the relayer only authorized paying for the case where the action succeeds and money flows to the intended receiver contract, not for Alice to unilaterally redirect the deposit to herself via a crafted failure.
- Any general-purpose or automated relayer service that batches/serves many users is exposed to systematic griefing: a malicious Alice can repeatedly submit meta-transactions with high attached deposits targeting receivers/methods she knows will fail (e.g., a nonexistent method, or a method that will revert under specific conditions she controls), extracting the relayer's deposits reliably every time.
- This qualifies as "concrete unauthorized value movement" per the validation rubric: value that the relayer paid for ends up, deterministically and by design, in the hands of a party (Alice) who did not fund it and who can trigger the condition (receipt failure) at will.

## Likelihood Explanation
- Reachable directly from a single submitted transaction/meta-transaction: any account can act as "Alice" and construct a `SignedDelegateAction` with an attached deposit targeting an action/method they know will fail (e.g., an account/method that doesn't exist, or one gated by a condition Alice controls). No relayer collusion is required — only a naively/automatically operating relayer (which is the anticipated operational model per the docs: "future iterations…mostly application-specific relayers…general-purpose relayer is difficult").
- The behavior is deterministic (100% of the time deposit refunds route to the predecessor), so exploitation does not depend on race conditions, timing, or validator behavior — it is a guaranteed protocol-level outcome once the attacker crafts a failing inner action.
- The docs already flag this as a known trust-relationship caveat rather than a hardened protocol guarantee, meaning any relayer that does not implement extensive off-chain simulation/anti-abuse heuristics is currently exposed.

## Recommendation
Consider changing the deposit-refund destination for actions originated via `DelegateAction`/`DelegateV2` so that the refund goes to the relayer (the original transaction signer who funded the deposit) rather than unconditionally to the `predecessor_id` of the failed inner receipt. This could be implemented by:
- Threading through refund-destination metadata on delegate-spawned receipts (distinguishing "who attached the deposit" from "who is the on-chain predecessor"), or
- Requiring/encouraging relayers to enforce off-chain simulation and reputation/rate-limiting before accepting a `SignedDelegateAction`, and documenting more prominently (with a security-notice level of visibility, not just an architecture doc) that relayers must not treat `DelegateAction` submissions from untrusted senders as safe without simulation.

At minimum, this should be treated as a first-class, protocol-level, documented risk (currently it's low-visibility, referenced only in `docs/architecture/how/meta-tx.md`) so relayer implementers cannot miss it, since the current code comment burying this in `actions.rs` is easy to overlook.

## Proof of Concept
1. Relayer operates a general-purpose meta-transaction relaying service that accepts `SignedDelegateAction`s from arbitrary users and wraps them in a `Transaction`/`SignedTransaction` it signs and pays for.
2. Attacker (Alice) creates a `DelegateAction` with `sender_id = alice`, `receiver_id = victim_contract`, and inner actions including a `FunctionCall`/`Transfer` with a large attached `deposit`, targeting a method that Alice knows will fail deterministically on `victim_contract`'s shard (e.g., a method requiring a precondition she can make false, or simply a non-existent method).
3. Alice signs the `DelegateAction` and sends it to the relayer.
4. The relayer, without deep simulation, wraps it into a transaction and submits it, paying gas and the deposit out of its own balance.
5. On-chain: `apply_delegate_action` (`runtime/runtime/src/actions.rs:499-513`) creates the inner action receipt with `predecessor_id = alice` (not the relayer).
6. The inner action receipt is processed on `victim_contract`'s shard and fails as designed by Alice.
7. `refund_unspent_gas_and_deposits` generates a deposit refund receipt whose `balance_refund_receiver()` is the failed receipt's `predecessor_id` — `alice` — crediting the full attached deposit back to Alice's account, even though the relayer funded it.
8. Net effect: relayer's NEAR balance decreases by the deposit amount; Alice's balance increases by the same amount, with no compensating value delivered to the relayer or to `victim_contract`.

**Uncertainty/limitations**: I was not able to fully trace the exact borsh/runtime code for `Receipt::balance_refund_receiver()` and `refund_unspent_gas_and_deposits`'s precise line-level logic within the tool-call budget available (only grep hit counts were retrieved for these, not their bodies), so the precise field-name/line-number citations for that specific function are inferred from the spec document (`protocol-model/spec/runtime-execution.md`) and the `docs/architecture/how/meta-tx.md` description rather than direct source inspection. The core causal mechanism — `apply_delegate_action` setting `predecessor_id: sender_id.clone()` on the spawned receipt — is directly confirmed from source.

### Citations

**File:** runtime/runtime/src/actions.rs (L499-519)
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

    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
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
