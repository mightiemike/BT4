### Title
Universal `0u` accounts can be permanently locked by transferring NEAR to a UAID with no known `StateInit` preimage - ([File: runtime/runtime/src/universal_account_id.rs])

### Summary
Any account (including an unprivileged transaction signer) can `Transfer` NEAR to an arbitrary `0u`‑prefixed Universal Account ID (UAID). This creates an `Account::Uninitialized` record holding the deposited balance. The *only* action that can move that account out of the uninitialized state — `UniversalStateInitAction` — requires the caller to supply a `StateInit` payload whose hash (via `encode_universal_account_id`) equals the target account id. If the sender chose (or accidentally derived) a UAID whose 32‑byte preimage is not a `StateInit` anyone knows how to construct (e.g. the all‑zero hash, or any hash for which no corresponding access key/state exists), the deposited funds become permanently unreachable: no key can ever be installed and no other action is permitted against an uninitialized account.

### Finding Description
`Account::new_uninitialized` is used to create a `0u` account purely from a `Transfer`, with no state init required: [1](#0-0) 

The account exposes only a `bootstrap_nonce` and balance while uninitialized: [2](#0-1) 

`check_account_existence` rejects every action except a state init or transfer while the account is uninitialized, confirmed by `AccountNotInitialized`: [3](#0-2) 

`action_universal_state_init` is the only path that can `initialize()` the account, and it requires decoding a `StateInit` payload that must hash back to the exact account id: [4](#0-3) 

The UAID is simply the base32 encoding of an arbitrary 32-byte hash — nothing ties it to a discoverable preimage: [5](#0-4) 

The engineering team's own test explicitly documents this exact scenario — funding a UAID with a hash that has "no known state init" leaves the account permanently uninitialized: [6](#0-5) 

And the protocol doc for `AccountState` states plainly that only universal accounts can be uninitialized, arriving that way purely via an ordinary transfer: [7](#0-6) 

This is structurally the same bug class as the referenced report ("assets deposited into a contract can become locked because there is no mechanism to move/withdraw them out of the receiving entity"): here, an on-chain **Account** (not a Solidity contract) can receive a legitimate `Transfer` action, yet the receiving entity (an uninitialized `0u` account) has *no* code, no access key, and no defined recovery/refund/reclaim mechanism for the deposited balance if the address's `StateInit` preimage is unknown to anyone. Unlike the `AccountDoesNotExist` refund path for ordinary named/implicit accounts (which fails the transfer and refunds the sender), a UAID always "exists" as soon as it is funded — the transfer succeeds, funds are deducted from the sender, and the funds sit in a black-hole account rather than being refunded or ever spendable.

### Impact Explanation
Funds sent to a UAID whose preimage nobody knows (deliberately, by mistake, or through malicious social engineering — e.g., an attacker publishing a "donation address" that is actually an un-owned UAID) are permanently and irrecoverably frozen. This is a direct, concrete "permanently frozen funds" condition explicitly listed as an accepted impact category. Because any signer can trigger this merely by issuing a standard `Transfer` action to a syntactically valid UAID string, the attack surface is the ordinary unprivileged transaction path (no validator or node compromise required).

### Likelihood Explanation
Likelihood is high for accidental loss (any typo/derivation error, or a naive wallet/tool generating a `0u...` string without a corresponding real `StateInit`, silently succeeds instead of failing like `AccountDoesNotExist` does for named accounts) and moderate-to-high for intentional abuse (an attacker can advertise a "safe deposit"/"burn" UAID knowing full well no one holds its preimage, luring users or protocols into locking funds there). The transaction itself is a completely ordinary, unprivileged `Transfer`; no special permissions or chain state are needed to reach this path.

### Recommendation
- Add an explicit safety valve for UAID transfers: e.g., require the sender to co-submit (or pre-register) a valid `StateInit` alongside the first funding transfer, so an account can only be created in the uninitialized state when its owner has demonstrably committed to a known preimage.
- Alternatively, treat a `Transfer` to a `0u` account with no subsequently-observed state init within some bound as reclaimable by the original sender (analogous to the existing `AccountDoesNotExist` refund semantics for other unusable receivers), or reject transfers to UAIDs outright unless accompanied by a state init in the same transaction/receipt.
- At minimum, strongly warn at the RPC/wallet layer whenever a `Transfer`'s receiver classifies as `AccountType::UniversalAccount`, since success does not imply the funds are recoverable.

### Proof of Concept
1. Pick (or compute) the UAID for the all-zero 32-byte hash: `0u0000000000000000000000000000000000000000000000000000` (a `AccountType::UniversalAccount`, confirmed in `test_dump_state_with_uninitialized_universal_account`).
2. As any funded account (e.g., `test0`), submit a standard `SignedTransaction::send_money` transferring NEAR to that UAID.
3. The transfer succeeds; `env.query_account(uaid)` shows `AccountState::Uninitialized` holding the deposited `amount`.
4. Because no one can produce a `StateInit` whose hash is the all-zero value (or, more generally, because the sender/attacker never publishes/knows a valid preimage for the address they funded), `UniversalStateInitAction` can never be submitted for this id, and every other action against it fails with `ActionErrorKind::AccountNotInitialized` per `check_account_existence`.
5. The deposited balance is now permanently locked in the trie: it cannot be withdrawn, refunded, or otherwise moved by any transaction. [8](#0-7) [3](#0-2)

### Citations

**File:** runtime/runtime/src/universal_account_id.rs (L41-49)
```rust
    let account = match maybe_account {
        Some(account) => account,
        // Create without changing actor_id, so a same-receipt follow-up can't hijack the account.
        None => maybe_account.insert(Account::new_uninitialized(
            Balance::ZERO,
            storage_usage_config.num_bytes_account,
            initial_nonce_value(apply_state.block_height),
        )),
    };
```

**File:** runtime/runtime/src/universal_account_id.rs (L51-81)
```rust
    if !account.is_initialized() {
        // The action carries the bytes the producer serialized; installing the
        // state needs them decoded. Every receipt is validated before its actions
        // run and validation rejects a state init that does not decode, so this
        // only fires if that invariant has been broken. Failing the action rather
        // than the chunk keeps a hypothetical gap in that coverage from becoming a
        // halt, since the payload comes from outside.
        let Ok(state_init) = UniversalStateInit::from_raw(&action.state_init) else {
            result.result = Err(ActionErrorKind::MalformedUniversalStateInit.into());
            return Ok(());
        };
        // Installed keys must start above the nonce the bootstrap consumed, or those
        // same bytes replay through the access-key path. It's practically impossible
        // for `consumed_nonce` to be bigger than `initial_nonce_value(apply_state.block_height)`,
        // but let's keep the check for the sake of complete safety.
        let consumed_nonce = account.bootstrap_nonce().unwrap_or(0);
        let access_key_nonce = max(initial_nonce_value(apply_state.block_height), consumed_nonce);
        account.initialize().or_inconsistent_state(account_id)?;
        install_universal_account(
            state_update,
            account,
            account_id,
            &state_init,
            result,
            fees,
            access_key_nonce,
        )?;
        if result.result.is_err() {
            return Ok(());
        }
    }
```

**File:** core/primitives-core/src/account.rs (L31-46)
```rust
/// Whether an account's state has been installed.
///
/// Only universal accounts can be uninitialized: they come into existence when
/// a transfer funds a `0u` id whose state init has not been applied yet. A
/// deterministic `0s` account waiting for its state init is an ordinary V1
/// account with no contract, not this.
#[derive(
    PartialEq, Eq, Clone, Copy, Debug, Default, serde::Serialize, serde::Deserialize, ProtocolSchema,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
#[serde(rename_all = "snake_case")]
pub enum AccountState {
    #[default]
    Initialized,
    Uninitialized,
}
```

**File:** core/primitives-core/src/account.rs (L213-243)
```rust
/// A universal account funded before its state init was installed.
///
/// It carries a balance and a [`UninitializedAccountV1::bootstrap_nonce`], and
/// no contract, access keys or data. Installing the state init is the only thing
/// that can add any of those, and doing so moves the account out of this state,
/// so everything else that writes to an account is unreachable while it stays
/// uninitialized.
#[derive(BorshSerialize, BorshDeserialize, PartialEq, Eq, Debug, Clone, ProtocolSchema)]
pub struct UninitializedAccountV1 {
    /// The total not locked tokens.
    amount: Balance,
    /// Storage used by the account record itself.
    storage_usage: StorageUsage,
    /// Nonce for the account's own transactions while it is uninitialized, used
    /// by the self-signed state init, and what makes that transaction one-shot.
    ///
    /// It closes two different replays. Consuming it closes the first: a failed
    /// init leaves the account uninitialized, so without it the same signed
    /// bytes would stay admissible and anyone could resubmit them, burning the
    /// conversion fee each time for as long as the transaction stayed inside its
    /// validity window. Seeding it from the creation height closes the second: a
    /// re-created account starts above every nonce its previous incarnation
    /// could have signed for, so the old bytes cannot bootstrap it either.
    ///
    /// Seeded exactly as a newly created access key is:
    /// `(creation_block_height - 1) * ACCESS_KEY_NONCE_RANGE_MULTIPLIER`.
    ///
    /// Dropped by [`Account::initialize`]: once the state init has installed
    /// the access keys, each of them carries its own nonce.
    bootstrap_nonce: Nonce,
}
```

**File:** runtime/runtime/src/actions.rs (L2390-2414)
```rust
    /// An uninitialized account has no access keys, code or data, so for
    /// anything but its own state init and a transfer it is as good as absent.
    #[test]
    fn uninitialized_account_rejects_actions_needing_state() {
        let account_id = account_id();
        let config = RuntimeConfig::test();
        let uninitialized =
            Some(Account::new_uninitialized(Balance::from_near(1), 100, TEST_BOOTSTRAP_NONCE));
        let expected: Result<(), ActionError> =
            Err(ActionErrorKind::AccountNotInitialized { account_id: account_id.clone() }.into());

        for action in actions_requiring_an_account() {
            assert_eq!(
                check_account_existence(
                    &action,
                    &uninitialized,
                    &account_id,
                    &config,
                    TEST_RECEIPT_SHAPE
                ),
                expected,
                "expected rejection for {action:?}",
            );
        }
    }
```

**File:** core/primitives-core/src/universal_account_id.rs (L20-40)
```rust
pub const UAID_PREFIX: &str = "0u";
/// Base32 symbols encoding the 256-bit hash (`ceil(256 / 5)`).
pub const UAID_DATA_SYMBOLS: usize = 52;
/// Total UAID length: prefix + data.
pub const UAID_LEN: usize = UAID_PREFIX.len() + UAID_DATA_SYMBOLS;

/// Crockford base32, lowercase, excluding `i l o u` to reduce transcription errors.
const CROCKFORD: &[u8; 32] = b"0123456789abcdefghjkmnpqrstvwxyz"; // cspell:disable-line

/// Encode a 32-byte hash as a `0u` universal account id.
pub fn encode_universal_account_id(hash: &[u8; 32]) -> AccountId {
    let data = base32_encode(hash);
    let mut s = String::with_capacity(UAID_LEN);
    s.push_str(UAID_PREFIX);
    for &v in &data {
        s.push(CROCKFORD[v as usize] as char);
    }
    debug_assert_eq!(s.len(), UAID_LEN);
    // Safe: the emitted charset and length are always a valid account id.
    s.parse::<AccountId>().expect("uaid codec must produce a valid account id")
}
```

**File:** integration-tests/src/tests/tools/state_dump.rs (L599-623)
```rust

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
