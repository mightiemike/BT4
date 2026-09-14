## Title
Validator/Account-Controlled Growth of `storage_usage` Permanently Blocks `DeleteAccount`, Freezing the Storage-Staked Balance - (File: `runtime/runtime/src/actions.rs`)

### Summary
The Sherlock finding describes a validator-controlled array (`Unstaking`) that grows unboundedly through normal protocol usage and is checked against a fixed cap (`300`) on a later, unrelated operation (`setValidatorAddress`), permanently blocking that operation once the cap is exceeded. The closest reachable analog in nearcore is `Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE`, a fixed cap of `10_000` bytes checked in `action_delete_account`. An account's `storage_usage` can be grown without any upper bound by ordinary, self-signed transactions (`AddKey`, `DeployContract`, `DeployGlobalContract`+`UseGlobalContract`, gas-key installs, contract-data writes via `FunctionCall`), and once `storage_usage` (minus contract-code bytes) exceeds the fixed cap, `DeleteAccount` permanently and irreversibly fails with `DeleteAccountWithLargeState`, with no code path to shrink `storage_usage` back under the cap other than deleting individual keys/data one by one (subject to gas limits per receipt).

### Finding Description
`Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE` is a hardcoded constant (`10_000` bytes) intended to bound the cost of deleting an account's trie entries in one receipt: [1](#0-0) 

`action_delete_account` computes the account's storage usage (excluding contract code bytes) and rejects deletion outright if it exceeds the cap, leaving the account entirely unchanged (no state is removed, no refund receipt is produced): [2](#0-1) 

This is confirmed to leave the account byte-for-byte unchanged on rejection: [3](#0-2) 

Critically, nothing in the protocol prevents `storage_usage` from growing past this cap through completely ordinary, permission-checked actions the account owner performs on their own account — `AddKey` (each key incrementally adds `access_key_storage_usage`), gas keys (each carrying up to `MAX_NONCES_FOR_GAS_KEY` nonce records), and contract-data writes from `FunctionCall` execution: [4](#0-3) 

Storage staking (`check_storage_stake`) only requires `amount + locked >= storage_amount_per_byte * storage_usage`; it never caps `storage_usage` itself, so it grows monotonically as the account is used, exactly the way the Sherlock report's `Unstaking` array grows monotonically as a validator stakes/unstakes: [5](#0-4) 

Once `storage_usage` (net of contract code) exceeds `10_000` bytes, `DeleteAccount` becomes permanently unusable for that account — there is no batch-removal or "purge" action; the only recourse is issuing individual `DeleteKey`/data-removal calls, each bounded by the gas and action limits of a single receipt, which itself may not be sufficient to bring a heavily-used account back under the cap within any single transaction. The account is described in the API schema itself as "whose state is large is temporarily banned" from deletion, showing this is a real, reachable state: [6](#0-5) 

The end effect mirrors the reported bug class precisely: an unbounded, protocol-permitted growth of size-tracked state tied to an account, checked against a static cap by an unrelated, later "reset"/"address-change"-equivalent operation (`DeleteAccount`), causing that operation to become permanently unusable once the threshold is crossed, with the account's storage-stake-backed balance portion (`storage_amount_per_byte * storage_usage`) left irrecoverable since it can only be reclaimed via successful deletion.

### Impact Explanation
Once an account's net storage usage exceeds `10_000` bytes, the account can never again be deleted, and the fraction of its balance that has to remain locked to satisfy the storage-staking invariant (`storage_amount_per_byte * storage_usage`) becomes permanently unreclaimable through the only protocol path (`DeleteAccount` → `Receipt::new_balance_refund`) designed to return it. This is a self-inflicted but protocol-enforced permanent freeze of funds, directly analogous to the validator being permanently locked out of `setValidatorAddress` once their `Unstaking` array crosses 300 entries.

### Likelihood Explanation
Any unprivileged account owner can trigger this simply through ordinary usage — repeatedly adding access keys, gas keys, or having a contract on their account write growing amounts of persistent storage — with no warning until the moment `DeleteAccount` is attempted and permanently rejected. No special privilege, validator status, or malicious peer is required; it is reachable purely through a sequence of normal, individually-valid transactions signed by the account holder.

### Recommendation
Provide a bounded, iterative way to shrink `storage_usage` below the cap (e.g., a dedicated "purge storage" action that removes N trie entries per call and is explicitly designed to be called repeatedly), or make the deletion cap dynamic/removed in favor of metering the deletion's compute cost against the receipt's gas budget directly (as is already done elsewhere, e.g. `storage_removes_compute` for gas-key nonce removal in `action_delete_account`), rather than an unconditional hard reject with no remediation path once the fixed threshold is crossed.

### Proof of Concept
1. Create an account and, via ordinary self-signed transactions, repeatedly submit `AddKey` actions (or gas-key installs, or contract calls that grow `ContractData` entries) until the account's `storage_usage` (net of local contract code) exceeds `Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE` (10,000 bytes) — demonstrated directly by the existing unit test `test_delete_account_too_large` and `test_delete_account_over_limit_leaves_account_unchanged`, which show a `storage_usage` of `MAX_ACCOUNT_DELETION_STORAGE_USAGE + 1` (or `+33`) is sufficient.
2. Submit a `DeleteAccount` action from that account (self as actor, matching `check_actor_permissions`'s requirement that `actor_id == account_id`).
3. Observe the receipt fails with `ActionErrorKind::DeleteAccountWithLargeState`, and the account remains completely unchanged — the balance, keys, and storage usage are all preserved as-is per `runtime/runtime/src/actions.rs:1297-1330`.
4. Because no action can reduce `storage_usage` faster than it can be re-grown in normal use, and no batch-removal action exists, the account's storage-stake-reserved balance is now permanently unreclaimable via `DeleteAccount`, the only protocol mechanism that returns it to a beneficiary.

### Citations

**File:** core/primitives-core/src/account.rs (L245-248)
```rust
impl Account {
    /// Max number of bytes an account can have in its state (excluding contract code)
    /// before it is infeasible to delete.
    pub const MAX_ACCOUNT_DELETION_STORAGE_USAGE: u64 = 10_000;
```

**File:** runtime/runtime/src/actions.rs (L340-369)
```rust
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1297-1330)
```rust
    #[test]
    fn test_delete_account_over_limit_leaves_account_unchanged() {
        let tries = TestTriesBuilder::new().build();
        let mut state_update =
            tries.new_trie_update(ShardUId::single_shard(), CryptoHash::default());
        let account_id: AccountId = "alice".parse().unwrap();
        let storage_usage = Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE + 33;
        let mut account = Some(Account::new(
            Balance::from_yoctonear(100),
            Balance::ZERO,
            AccountContract::Global(CryptoHash::default()),
            storage_usage,
        ));
        let mut actor_id = account_id.clone();
        let mut action_result = ActionResult::default();
        let receipt = Receipt::new_balance_refund(&"alice.near".parse().unwrap(), Balance::ZERO);
        let config = RuntimeConfig::test();

        let res = action_delete_account(
            &mut state_update,
            &mut account,
            &mut actor_id,
            &receipt,
            &mut action_result,
            &account_id,
            &DeleteAccountAction { beneficiary_id: "bob".parse().unwrap() },
            &config,
            ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage.protocol_version(),
        );
        assert!(res.is_ok());
        expect_delete_account_too_large(&action_result);
        let account_after = account.as_ref().expect("account must remain on failure");
        assert_eq!(account_after.storage_usage(), storage_usage);
    }
```

**File:** protocol-model/spec/accounts-keys.md (L41-42)
```markdown
1. **Regular key** (`add_regular_key`, `:230`): seeds `access_key.nonce = (block_height-1)*1_000_000` (`initial_nonce_value`, `:46`), writes it with `set_access_key`, and `checked_add`s `access_key_storage_usage` (`:17`) to the account's `storage_usage`. Storage usage uses `public_key.trie_id_len()` (the on-trie identifier length), so an ML-DSA-65 key — stored as a 33-byte SHA3-256 hash form — costs the same as ed25519 rather than its ~1953-byte raw form (`:26`, asserted by `test_ml_dsa_65_access_key_storage_scales`).
2. **Gas key** (`add_gas_key`, `:194`): forces the on-key `nonce` to 0 (`:206`), writes the access key, then writes `num_nonces` separate nonce entries each initialized to `initial_nonce_value(block_height)` via `set_gas_key_nonce` (`:212`). Storage usage uses `gas_key_storage_cost` (`:31`) = the access-key cost plus, per nonce, key length (`trie_id_len + size_of::<NonceIndex>`) + value length (`size_of::<Nonce>`) + `num_extra_bytes_record`.
```

**File:** protocol-model/spec/accounts-keys.md (L75-77)
```markdown
### Storage staking

`check_storage_stake` (`verifier.rs:48`) requires `amount + locked >= storage_amount_per_byte * storage_usage` (`:74`); shortfall returns `LackBalanceForStorageStaking(needed)`, surfaced to transactions as `LackBalanceForState`. **Exception**: a *zero-balance account* (NEP-448) — `storage_usage <= ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT = 770` bytes (`verifier.rs:25`,`:88`) — always passes regardless of balance. `storage_usage` is maintained incrementally by every action that adds/removes keys, code, or data (e.g. `access_key_storage_usage`; `action_deploy_contract` at `actions.rs:297`).
```

**File:** chain/jsonrpc/openapi/openapi.json (L2257-2277)
```json
          {
            "additionalProperties": false,
            "description": "Delete account whose state is large is temporarily banned.",
            "properties": {
              "DeleteAccountWithLargeState": {
                "properties": {
                  "account_id": {
                    "$ref": "#/components/schemas/AccountId"
                  }
                },
                "required": [
                  "account_id"
                ],
                "type": "object"
              }
            },
            "required": [
              "DeleteAccountWithLargeState"
            ],
            "type": "object"
          },
```
