This confirms the analog: any unprivileged transaction signer or RPC caller can send a `Transfer` action to an arbitrary `0u...` (`UniversalAccount`), `0x...` (`EthImplicitAccount`), or `0s...` (`NearDeterministicAccount`) id, and the runtime accepts and locks in the deposited balance unconditionally, without verifying that anyone possesses (or ever will produce) a state-init/private-key preimage that hashes to that id. If the id was derived from data nobody controls (e.g., mistyped, or deliberately from an unrecoverable hash), the account is stuck permanently in `Account::Uninitialized`/no-access-key state — inert for every action except its own (unreachable) state-init or another `Transfer` — with no protocol-level mechanism to reclaim or redirect the funds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unrecoverable permanent lock of NEAR balance via `Transfer` to an unowned implicit/universal/deterministic account id - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_implicit_account_creation_transfer` and `check_account_existence`/`implicit_creation_allowed` let any signer create and fund a `0u...` (`UniversalAccount`), `0x...` (`EthImplicitAccount`), or `0s...` (`NearDeterministicAccount`) account purely by sending a `Transfer`, with no check that the account id's derivation preimage (a `UniversalStateInit`/`DeterministicAccountStateInit`, or an ETH/ED25519 keypair) is known to anyone. If the id happens to correspond to no known preimage (accidental mistype, or a hash that nobody generated a preimage for), the resulting account permanently sits in the `Uninitialized`/no-access-key state holding the deposit, and the protocol provides no action or refund path to move that balance out. This directly mirrors the "locked funds without a withdraw path" bug class from the referenced report: value enters a state-machine slot for which no actor holds the authorization needed to move it back out.

### Finding Description
`check_account_existence` allows `Transfer` to create any of these implicit-family accounts (`Action::Transfer` branch, calling `implicit_creation_allowed`), and for `UniversalAccount` this is even allowed when the transfer is not the only action in the receipt. The created account is left `Uninitialized`, holding only a balance and no access key/contract: [5](#0-4) 

Once created, `check_account_existence` treats an uninitialized account as inert for anything but its own state-init action or another `Transfer`: [3](#0-2) 

The only way to move the account out of `Uninitialized` (and thereby gain the access keys needed to eventually spend the balance) is executing the matching `UniversalStateInitAction`/`DeterministicStateInitAction`, whose receiver id must equal the id deterministically derived from the state-init payload (`derive_universal_account_id` / `derive_near_deterministic_account_id`). Nothing in the transfer path verifies that such a payload exists or is known to anyone before the funds are locked in. The project's own test explicitly documents that a `0u` id "derived from the all-zero hash" has "no known state init, so the account can never leave the uninitialized state," and a `Transfer` to it succeeds and leaves the deposit permanently stuck: [6](#0-5) 

The same applies to `NearDeterministicAccount` (`0s...`) ids, which only differ from `UniversalAccount` in that they are always created initialized but with no code/keys until the exact matching `DeterministicStateInitAction` arrives — again gated purely on knowledge of the state-init preimage, not on any recoverability guarantee.

### Impact Explanation
Any account that satisfies the `UniversalAccount` (`0u` + 56 hex chars) or `NearDeterministic`/`EthImplicit` id shape but for which nobody holds the corresponding preimage becomes a one-way sink: NEAR sent there is permanently removed from circulation for that owner with no on-chain recovery mechanism, no beneficiary, and no protocol-provided refund/reclaim action. This is a genuine, protocol-level "locked funds" condition reachable by any ordinary transaction signer or RPC caller with a single `Transfer` action — no privileged role required.

### Likelihood Explanation
Triggering the lock requires only a normal `Transfer` transaction to a syntactically valid but semantically "unowned" implicit-family account id — something that can happen accidentally (a typo in a `0u.../0s.../0x...` address, or copying a stale/incorrectly derived id from a dApp) or deliberately. No special privileges, timing, or contract deployment are needed, and the runtime accepts it as a completely valid, fee-charged transaction.

### Recommendation
Consider adding a recovery path analogous to what the referenced judge suggested for locked payable functions: e.g., allow the predecessor of the triggering `Transfer` (or a later transaction from the original sender, tracked similarly to `refund_to_account_id`) to reclaim balance sitting in a still-`Uninitialized` universal/deterministic account after some timeout, or require the state-init/id relationship to be pre-validated (e.g., by requiring the `UniversalStateInitAction`/`DeterministicStateInitAction` to accompany the very first transfer in the same receipt) so that funds are never accepted into a slot whose unlocking preimage is unverified.

### Proof of Concept
1. Compute (or randomly pick) any 56-hex-character string `H` and form the account id `0u` + `H`; do not compute/know any `UniversalStateInit` whose `derive_universal_account_id` equals that id (this is exactly the scenario the repo's own `test_dump_state_with_uninitialized_universal_account` test constructs from the all-zero hash — see [7](#0-6) ).
2. As any funded account, submit `SignedTransaction::send_money(nonce, sender, "0u"+H, signer, deposit, block_hash)`.
3. The transaction succeeds; `view_account("0u"+H)` shows `state: Uninitialized` and `amount: deposit` (mirrors the test assertions at lines 620-623).
4. Because no `UniversalStateInitAction` payload hashing to `0u"+H` exists or is derivable by anyone, no transaction can ever install state on this account (per `action_universal_state_init`'s requirement that the receiver id equal the state-init-derived id), so `deposit` is permanently locked with no withdraw/refund action available in the protocol.

### Citations

**File:** runtime/runtime/src/actions.rs (L270-287)
```rust
        AccountType::NearDeterministicAccount => {
            *account = Some(create_deterministic_account(
                deposit,
                &apply_state.config.fees.storage_usage_config,
            ));
        }
        AccountType::UniversalAccount => {
            *account = Some(Account::new_uninitialized(
                deposit,
                fee_config.storage_usage_config.num_bytes_account,
                initial_nonce_value(block_height),
            ));
        }
        // Unreachable: this is an implicit account creation transfer, so
        // `check_account_existence` has already turned away every receiver that
        // `implicit_creation_allowed` refuses.
        AccountType::NamedAccount => panic!("must be implicit"),
    }
```

**File:** runtime/runtime/src/actions.rs (L880-888)
```rust
            // An uninitialized `0u` account has no access keys, code or data, so
            // for everything but its own state init and a transfer it is as good
            // as absent.
            if !account.is_initialized() {
                return Err(ActionErrorKind::AccountNotInitialized {
                    account_id: account_id.clone(),
                }
                .into());
            }
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

**File:** integration-tests/src/tests/tools/state_dump.rs (L589-623)
```rust
/// An uninitialized `0u` account must survive a state dump: Genesis validation
/// should accept such accounts.
#[test]
// TODO(spice-test): Assess if this test is relevant for spice and if yes fix it.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_dump_state_with_uninitialized_universal_account() {
    init_test_logger();

    let epoch_length = 4;
    let (store, genesis, mut env, near_config) = setup(epoch_length, PROTOCOL_VERSION, false);

    // A `0u` id derived from the all-zero hash: valid, and with no known state
    // init, so the account can never leave the uninitialized state.
    let uaid: AccountId = "0u0000000000000000000000000000000000000000000000000000".parse().unwrap();
    assert_eq!(uaid.get_account_type(), AccountType::UniversalAccount);

    let deposit = Balance::from_near(1);
    let genesis_hash = *env.clients[0].chain.genesis().hash();
    let signer = InMemorySigner::test_signer(&"test0".parse().unwrap());
    let tx = SignedTransaction::send_money(
        1,
        "test0".parse().unwrap(),
        uaid.clone(),
        &signer,
        deposit,
        genesis_hash,
    );
    assert_eq!(env.rpc_handlers[0].process_tx(tx, false, false), ProcessTxResponse::ValidTx);

    safe_produce_blocks(&mut env, 1, epoch_length * 2 + 1);

    // The transfer left an uninitialized account behind.
    let view = env.query_account(uaid.clone());
    assert_eq!(view.state, AccountState::Uninitialized);
    assert_eq!(view.amount, deposit);
```
