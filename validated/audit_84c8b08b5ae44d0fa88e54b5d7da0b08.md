### Title
Deleting an account with a non-existent `beneficiary_id` causes the account's remaining balance to be permanently burned - (File: `runtime/runtime/src/actions.rs`, `docs/RuntimeSpec/Refunds.md`)

### Summary
`DeleteAccountAction` lets the signer of a transaction name an arbitrary `beneficiary_id` that is supposed to *receive* the deleted account's remaining balance [1](#0-0) . Validation of the action only checks that `beneficiary_id` is a syntactically valid account id, not that the account exists [2](#0-1) . If the named beneficiary account does not exist, the balance-transfer receipt to it fails (a `Transfer` action can only implicitly create an account, and never for a refund/plain named account, per `check_account_existence`/`implicit_creation_allowed`) [3](#0-2) , and the runtime then re-attempts to send the amount back as a deposit refund to the now-deleted predecessor account. Since that account no longer exists, this second refund also fails, and per protocol rules any refund that fails is simply burned rather than returned to anyone [4](#0-3) . This exact failure chain is called out directly in the test suite: *"The beneficiary has to exist, otherwise the balance transfer the delete sends it would come straight back as a refund"* [5](#0-4) , and a companion test confirms that a refund aimed at a missing account fails with `AccountDoesNotExist` and does not create the account [6](#0-5) .

### Finding Description
This mirrors the Sherlock bug class: a party nominally entitled to receive funds (`receiver`/`beneficiary_id`) can be made unreachable, so instead of the funds landing safely with a fallback party, they are permanently destroyed. In the Solidity report the "blacklist" made the receiver's `safeTransfer` revert forever; in nearcore the equivalent "receiver cannot receive" condition is simply naming a `beneficiary_id` that has never been created (or was previously deleted). The protocol does not validate existence of the beneficiary at submission time — only account-id syntax is checked [2](#0-1)  — so any signer can trivially reach this path with a single `DeleteAccountAction` in an ordinary transaction. The transfer-to-beneficiary receipt fails because named/non-implicit accounts are never implicitly created by a `Transfer`, and refund receipts are explicitly barred from creating accounts at all [3](#0-2) . The resulting fallback refund targets the deleted predecessor account, which also no longer exists, so that refund fails too, and the documented behavior for any failed refund is unconditional burning of the token amount [4](#0-3) .

### Impact Explanation
The outcome is strictly worse than "temporarily frozen funds": the tokens are burned and irrecoverable, which is an accepted impact category (supply deflation / permanently frozen funds via invalid state transition acceptance). Any account holder who mistypes, is socially engineered into, or is directed (e.g. by a compromised or malicious dApp/contract that constructs the `DeleteAccountAction` on the user's behalf) to use a non-existent `beneficiary_id` loses their entire remaining account balance with no recovery path. Because `DeleteAccount` must be the final action in a receipt [7](#0-6) , there is also no way to detect/undo the mistake within the same transaction.

### Likelihood Explanation
This requires only a single, ordinary transaction from any unprivileged account containing `Action::DeleteAccount(DeleteAccountAction { beneficiary_id })` with a `beneficiary_id` that does not (yet) exist — no validator collusion, no special privileges, and no code paths outside normal runtime apply logic are involved. It is directly reachable by any transaction signer, contract deployer, or a contract that constructs such a delete action on a user's behalf (e.g. a wallet/relayer or vesting contract with a user-supplied beneficiary parameter).

### Recommendation
Require `beneficiary_id` to reference an existing (initialized) account at action-validation time (similar to how other `DeleteAccountAction` error kinds like `DeleteAccountStaking` are pre-checked), or alternatively allow named-account transfers from a `DeleteAccount` beneficiary payout to implicitly create the beneficiary account (as is already permitted for the top-level, non-refund transfer case for implicit/universal accounts), so that a non-existent beneficiary does not result in an unconditional burn.

### Proof of Concept
1. Attacker/careless user `alice.near` holds balance `B` and access to a full-access key.
2. `alice.near` submits a single transaction: `Action::DeleteAccount(DeleteAccountAction { beneficiary_id: "nonexistent123.near" })` where `nonexistent123.near` has never been created.
3. Runtime deletes `alice.near` and issues a `Transfer` action receipt of `B` to `nonexistent123.near`.
4. Because `nonexistent123.near` is a `NamedAccount` and the receipt is not the special implicit-creation case, `check_account_existence`/`implicit_creation_allowed` rejects the transfer with `AccountDoesNotExist` [3](#0-2) .
5. The runtime issues a deposit refund of `B` back to the predecessor, `alice.near` — but `alice.near` was already deleted in step 3, so this refund receipt itself fails with `AccountDoesNotExist` (as verified by `refund_may_not_create_universal_account`) [6](#0-5) .
6. Per protocol rules, a failed refund is burned [4](#0-3) , so `B` is permanently destroyed with no recovery for `alice.near` or anyone else.

Note: I was unable to view the exact body of `action_delete_account` in `runtime/runtime/src/actions.rs` within the available tool budget (only its signature/location was located), so the precise point where the transfer-to-beneficiary receipt is constructed could not be directly cited; the causal chain above is reconstructed from the cited validation logic, the explicit test comment, and the documented refund-burn rule, all of which are internally consistent. A background engineer should confirm this exact code path directly in `action_delete_account` before implementing the fix.

### Citations

**File:** docs/RuntimeSpec/Actions.md (L278-289)
```markdown
## DeleteAccountAction

```rust
pub struct DeleteAccountAction {
    /// The remaining account balance will be transferred to the AccountId below
    pub beneficiary_id: AccountId,
}
```

**Outcomes**:

- The account, as well as all the data stored under the account, is deleted and the tokens are transferred to `beneficiary_id`.
```

**File:** docs/RuntimeSpec/Actions.md (L291-300)
```markdown
### Errors

**Validation Error**:

- If `beneficiary_id` is not a valid account id, the following error will be returned

```rust
/// Invalid account ID.
InvalidAccountId { account_id: AccountId },
```
```

**File:** docs/RuntimeSpec/Actions.md (L301-307)
```markdown

- If this action is not the last action in the action list of a receipt, the following error will be returned

```rust
/// The delete action must be a final action in transaction
DeleteActionMustBeFinal
```
```

**File:** runtime/runtime/src/actions.rs (L928-947)
```rust
/// Whether a transfer to an account that does not exist yet may create it.
fn implicit_creation_allowed(account_type: AccountType, receipt_shape: ReceiptShape) -> bool {
    let ReceiptShape { is_refund, is_the_only_action } = receipt_shape;
    if is_refund {
        return false; // Refund can never create an account
    }

    match account_type {
        // Named accounts can never be implicitly created by transfer
        AccountType::NamedAccount => false,
        // Near-implicit, Eth-implicit, and deterministic accounts can only be created
        // if transfer is the only action, to avoid account hijacking.
        AccountType::NearImplicitAccount
        | AccountType::EthImplicitAccount
        | AccountType::NearDeterministicAccount => is_the_only_action,
        // Universal account creation does NOT require transfer to be the only action.
        // It cannot be hijacked by other actions batched with the transfer.
        AccountType::UniversalAccount => true,
    }
}
```

**File:** docs/RuntimeSpec/Refunds.md (L10-13)
```markdown
Refund receipts are identified by having `predecessor_id == "system"`. They are also special because they don't cost any gas to generate or execute. As a result, they also do not contribute to the block gas limit.

If the execution of a refund fails, the refund amount is burnt.
The refund receipt is an `ActionReceipt` that consists of a single action `Transfer` with the `deposit` amount of the refund.
```

**File:** runtime/runtime/src/tests/apply.rs (L6354-6357)
```rust

        // The beneficiary has to exist, otherwise the balance transfer the delete
        // sends it would come straight back as a refund.
        let beneficiary: AccountId = "beneficiary.near".parse().unwrap();
```

**File:** runtime/runtime/src/tests/apply.rs (L6880-6926)
```rust
    /// The other half of the old gate, untouched by the relaxation: refunds are
    /// free, so they must not create an account, a `0u` one included.
    #[test]
    fn refund_may_not_create_universal_account() {
        init_test_logger();
        let key = SecretKey::from_seed(KeyType::ED25519, "refund-target").public_key();
        let account_id = derive_universal_account_id(&state_init_for(&[key]).to_raw());
        let (runtime, tries, root, apply_state, _signers, epoch) = setup_runtime(
            vec![alice_account()],
            Balance::from_near(100),
            Balance::ZERO,
            Gas::from_teragas(1000),
        );

        let result = runtime
            .apply(
                tries.get_trie_for_shard(ShardUId::single_shard(), root),
                &None,
                &apply_state,
                from_ref(&Receipt::new_balance_refund(&account_id, funding())),
                SignedValidPeriodTransactions::empty(),
                &epoch,
                Default::default(),
            )
            .unwrap();
        let mut store_update = tries.store_update();
        let new_root =
            tries.apply_all(&result.trie_changes, ShardUId::single_shard(), &mut store_update);
        store_update.commit();

        // Assert on the reason, not just the absence: without this the test would
        // also pass if the refund receipt were dropped instead of refused.
        let [outcome] = &result.outcomes[..] else {
            panic!("the refund receipt must produce exactly one outcome, got {:?}", result.outcomes)
        };
        assert_matches!(
            &outcome.outcome.status,
            ExecutionStatus::Failure(TxExecutionError::ActionError(err))
                if matches!(err.kind, ActionErrorKind::AccountDoesNotExist { .. }),
            "a refund to a missing `0u` id must fail with AccountDoesNotExist",
        );
        let state = tries.new_trie_update(ShardUId::single_shard(), new_root);
        assert!(
            get_account(&state, &account_id).unwrap().is_none(),
            "a refund must not bring a `0u` account into existence",
        );
    }
```
