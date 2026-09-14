### Title
Function-call access key `allowance` is not enforced when the key is used to sign a meta-transaction `DelegateAction`, letting a scoped key spend beyond its signed financial limit - (File: docs/architecture/how/meta-tx.md)

### Summary
`FunctionCallPermission.allowance` is the on-chain, signature-bound financial cap an account owner attaches when granting a scoped, limited-privilege key (analogous to the TOFT `permit`-style approval in the original report: a signed authorization meant to bound how much value/usage a third party can extract). The nearcore docs explicitly confirm that this bound is skipped entirely when the same key is used to sign a NEP-366 `DelegateAction` relayed by any third party, because "for allowance, however, there is no check. All costs have been covered by the relayer" [1](#0-0) .

### Finding Description
`FunctionCallPermission` grants a key limited rights to call a specific `receiver_id`/`method_names`, bounded by an `allowance` — "a balance limit to use by this access key to pay for function call gas and transaction fees" [2](#0-1) . When such a key signs an ordinary transaction, the runtime verifier checks `verify_function_call_permission` and enforces the allowance against `gas_key_info`/account balance before admitting the transaction [3](#0-2) .

However, the same key can instead be used to sign a `DelegateAction` (meta transaction), which is a completely separate action-authorization path: the user signs `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`, and any relayer (unrelated third party) wraps and submits it, paying all gas and fees itself [4](#0-3) . As documented directly in the architecture notes: "Function access keys can limit the allowance, the receiving contract, and the contract methods… But first, both the methods and the receiver will be checked as expected… For allowance, however, there is no check. All costs have been covered by the relayer. Hence, even if the allowance of the key is insufficient to make the call directly, indirectly through meta transaction it will still work" [1](#0-0) . The docs go on to state this is "circumventable by going through a relayer" [5](#0-4) .

This exactly mirrors the report's bug class: a value-bounding, signature-based permission (`allowance` ≈ the ERC-20-style permit) that is checked on one call path (direct transaction) but silently skipped on an alternative path reachable by any unprivileged third party (relayer wrapping the same signed authorization in a `DelegateAction`), letting the holder/relayer use the scoped key far beyond the financial exposure the account owner intended when creating it — with `receiver_id`/`method_names` restrictions remaining the only real limits.

### Impact Explanation
Function-call access keys with a small `allowance` are the standard "defense-in-depth" mechanism NEAR account owners and dApps use to grant scoped, low-trust session keys (e.g., web-app session keys, game session keys) while capping financial exposure if the key is exfiltrated or the granting logic is otherwise abused. This mechanism is completely bypassed for any actor able to relay a `DelegateAction` signed with that key: the allowance is never checked, so the key can be used an unbounded number of times against the allowed `receiver_id`/method, with the relayer footing gas/deposit costs each time. Since anyone can act as the relayer (including the key holder itself or a malicious dApp/session-key custodian), the intended "financial circuit breaker" of the allowance provides no real protection once meta-transactions are usable, undermining a documented, load-bearing account-security assumption without violating the receiver/method scope checks that remain — a state where the runtime accepts calls it is documented and expected to reject, i.e., an invalid-transition-adjacent authorization bypass reachable purely by a normal RPC caller/relayer.

### Likelihood Explanation
High likelihood of reachability: it requires no privileged role, no leaked keys beyond the ordinary "app is holding a scoped key I gave it" trust model, and no network/validator manipulation — merely constructing a `SignedDelegateAction` with an existing, deliberately-limited-allowance `FunctionCallPermission` key and having any party submit it as the relayer, exactly as any legitimate meta-transaction relayer already does in production flows and in the codebase's own test helpers [6](#0-5) . The behavior is deterministic and already demonstrated/acknowledged in-repo, not a hypothetical.

### Recommendation
Enforce the `FunctionCallPermission.allowance` check (or an equivalent per-call spend accounting) when unwrapping and executing a `DelegateAction` signed by a function-call access key, mirroring the check performed in `verify_function_call_permission` for direct transactions, rather than only validating `receiver_id`/`method_names`. If the allowance semantics cannot be meaningfully translated to the meta-transaction cost model (since the relayer pays), the alternative is to explicitly disallow function-call access keys with a set `allowance` from signing `DelegateAction`s, or to document/require wallets to treat `allowance` as non-binding once meta-transactions are enabled, closing the gap between user expectation and actual protocol enforcement.

### Proof of Concept
1. Account owner creates a `FunctionCallPermission` access key with a small `allowance` (e.g., enough for 1-2 calls) restricted to `receiver_id = "app.near"`, `method_names = ["do_thing"]`, intending this to cap total usage/exposure.
2. A holder of this key (e.g., a dApp session, or an attacker who obtained the key through the normal low-trust distribution channel it was designed for) constructs a `DelegateAction { sender_id, receiver_id: "app.near", actions: [FunctionCall("do_thing", ...)], nonce, max_block_height, public_key }` and signs it with the key, per `SignedDelegateAction::sign` [7](#0-6) .
3. Any relayer (including the key holder itself acting as its own relayer, paying its own NEAR for gas) wraps this in an outer transaction it signs and submits, as in `meta_tx_from_actions` [8](#0-7) .
4. Per documented behavior, the receiver/method checks pass and the allowance check is skipped entirely, so `do_thing` executes even though the key's `allowance` was already exhausted or was set specifically to prevent more than one or two calls — confirmed by nearcore's own architecture documentation [9](#0-8) .
5. Repeating step 3 indefinitely lets the key be used without limit, defeating the account owner's intended spend/usage cap for that scoped key.

### Citations

**File:** docs/architecture/how/meta-tx.md (L244-260)
```markdown
## Function access keys in meta transactions

Assume alice sends a meta transaction and signs with a function access key.
How exactly are permissions applied in this case?

Function access keys can limit the allowance, the receiving contract, and the
contract methods. The allowance limitation acts slightly strange with meta
transactions.

But first, both the methods and the receiver will be checked as expected. That
is, when the delegate action is unwrapped on Alice's shard, the access key is
loaded from the DB and compared to the function call. If the receiver or method
is not allowed, the function call action fails.

For allowance, however, there is no check. All costs have been covered by the
relayer. Hence, even if the allowance of the key is insufficient to make the call
directly, indirectly through meta transaction it will still work.
```

**File:** docs/architecture/how/meta-tx.md (L261-266)
```markdown

This behavior is in the spirit of allowance limiting how much financial
resources the user can use from a given account. But if someone were to limit a
function access key to one trivial action by setting a very small allowance,
that is circumventable by going through a relayer. An interesting twist that
comes with the addition of meta transactions.
```

**File:** core/primitives-core/src/account.rs (L959-967)
```rust
pub struct FunctionCallPermission {
    /// Allowance is a balance limit to use by this access key to pay for function call gas and
    /// transaction fees. When this access key is used, both account balance and the allowance is
    /// decreased by the same value.
    /// `None` means unlimited allowance.
    /// NOTE: To change or increase the allowance, the old access key needs to be deleted and a new
    /// access key should be created.
    pub allowance: Option<Balance>,

```

**File:** runtime/runtime/src/verifier.rs (L594-619)
```rust
    if available_gas_key_balance < gas_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: available_gas_key_balance,
            cost: gas_cost,
        });
    }
    let new_gas_key_balance = gas_key_info.balance.checked_sub(gas_cost).unwrap();

    // Calculate new key balance in case of deposit failure. Charges only for the gas burned on
    // converting the transaction to a receipt.
    let Some(new_key_balance_on_deposit_failure) = gas_key_info.balance.checked_sub(burnt_amount)
    else {
        return TxVerdict::Failed(InvalidTxError::NotEnoughGasKeyBalance {
            signer_id: account_id.clone(),
            balance: gas_key_info.balance,
            cost: burnt_amount,
        });
    };

    // Validate FunctionCall permission constraints if applicable
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }
```

**File:** docs/RuntimeSpec/Actions.md (L340-366)
```markdown
```rust
/// The struct a user creates and signs to create a meta transaction.
struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

### Outcomes

- All actions inside `delegate_action.actions` are submitted with the `delegate_action.sender_id` as the predecessor, `delegate_action.receiver_id` as the receiver, and the relayer (predecessor of `DelegateAction`) as the signer.
- All gas and balance costs for submitting `delegate_action.actions` are subtracted from the relayer.
```

**File:** integration-tests/src/user/mod.rs (L283-318)
```rust
    /// Wrap the given actions in a delegate action and execute them.
    ///
    /// The signer signs the delegate action to be sent to the receiver. The
    /// relayer packs that in a transaction and signs it .
    fn meta_tx(
        &self,
        signer_id: AccountId,
        receiver_id: AccountId,
        relayer_id: AccountId,
        actions: Vec<Action>,
    ) -> Result<FinalExecutionOutcomeView, CommitError> {
        let inner_signer = create_user_test_signer(&signer_id);
        let user_nonce = self
            .get_access_key(&signer_id, &inner_signer.public_key())
            .expect("failed reading user's nonce for access key")
            .nonce;
        let delegate_action = DelegateAction {
            sender_id: signer_id.clone(),
            receiver_id,
            actions: actions
                .into_iter()
                .map(|action| NonDelegateAction::try_from(action).unwrap())
                .collect(),
            nonce: user_nonce + 1,
            max_block_height: 100,
            public_key: inner_signer.public_key(),
        };
        let signature = inner_signer.sign(delegate_action.get_nep461_hash().as_bytes());
        let signed_delegate_action = SignedDelegateAction { delegate_action, signature };

        self.sign_and_commit_actions(
            relayer_id,
            signer_id,
            vec![Action::Delegate(Box::new(signed_delegate_action))],
        )
    }
```

**File:** core/primitives/src/action/delegate.rs (L83-95)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }

    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** integration-tests/src/env/test_env.rs (L765-799)
```rust
    /// Wrap actions in a delegate action, put it in a transaction, sign.
    pub fn meta_tx_from_actions(
        &mut self,
        actions: Vec<Action>,
        sender: AccountId,
        relayer: AccountId,
        receiver_id: AccountId,
    ) -> SignedTransaction {
        let inner_signer = InMemorySigner::test_signer(&sender);
        let relayer_signer = InMemorySigner::test_signer(&relayer);
        let tip = self.clients[0].chain.head().unwrap();
        let user_nonce = tip.height + 1;
        let relayer_nonce = tip.height + 1;
        let delegate_action = DelegateAction {
            sender_id: inner_signer.get_account_id(),
            receiver_id,
            actions: actions
                .into_iter()
                .map(|action| NonDelegateAction::try_from(action).unwrap())
                .collect(),
            nonce: user_nonce,
            max_block_height: tip.height + 100,
            public_key: inner_signer.public_key(),
        };
        let signature = inner_signer.sign(delegate_action.get_nep461_hash().as_bytes());
        let signed_delegate_action = SignedDelegateAction { delegate_action, signature };
        SignedTransaction::from_actions(
            relayer_nonce,
            relayer,
            sender,
            &relayer_signer,
            vec![Action::Delegate(Box::new(signed_delegate_action))],
            tip.last_block_hash,
        )
    }
```
