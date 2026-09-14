## Finding

### Title
Permanently Unrecoverable Funds via Zero-Key Universal (`0u`) Account "Burn Address" - (File: `runtime/runtime/src/action_validation.rs`, `runtime/runtime/src/universal_account_id.rs`)

### Summary
The external report's root cause is a privileged-recovery mechanism (`exitAdministrator`) that, when left unset, is treated by the code as an intentional "safe" state (`getDeadStatus() == false`), yet in practice this "intended" no-op condition results in permanently stuck user funds with no path to remediation once quorum is unavailable. The nearcore analog is the `UniversalStateInit` ("`0u`") account scheme: a state init with an empty `access_keys` set and no `code` is explicitly treated as valid and intended ("the `0u` equivalent of a burn address"), but this "intended" degenerate configuration is indistinguishable from a normal account address to an ordinary sender, is reachable purely through unprivileged transactions, and results in unrecoverable funds with no owner, key, or administrator ever assignable — the same class of "intended-but-exploitable absence of a recovery path" as in the original report.

### Finding Description
A `0u` universal account id is derived deterministically as `SHA3-256` of the raw, borsh-encoded `UniversalStateInit` bytes [1](#0-0) . Action validation explicitly allows a state init with **both** `code: None` and `access_keys: BTreeSet::new()`, treating it as valid, "intended" behavior: [2](#0-1) 

Once any unprivileged party submits a `UniversalStateInit` action naming this derived id as receiver (this requires no proof of ownership of the account — only that the receiver id equals the hash of the supplied bytes, and it can be triggered by a relayer/any signer paying for the action, as demonstrated by `test_relayer_creates_funds_and_calls_in_one_tx` for the "code-only, no keys" variant), `action_universal_state_init` moves the account from `Uninitialized` to `Initialized` irreversibly: [3](#0-2) 

For the fully-empty state init (no code, no keys), this "initialization" installs literally nothing — no access key, no contract — leaving the account with a balance but zero mechanisms to ever move it: no signer can produce an access key that maps to the account's committed key set (there are none), and no contract exists to be called to release funds.

This mirrors the reported bug class precisely: an administrative/recovery capability (here, "having any spending authority over the account") is intentionally absent by design ("a burn address" — analogous to `exitAdministrator == address(0)` being read as "chain operating independently, working as intended"), but the consequence — funds becoming permanently and unconditionally stuck with zero quorum/validator/administrator path to ever reassign authority — is identical, and is reachable by an ordinary unprivileged transaction signer who is unaware the destination account id encodes a state init with no keys/code (these ids are opaque 55+ character strings, indistinguishable at a glance from any other valid `0u` account that *does* have an owner).

### Impact Explanation
Any transaction signer who transfers funds to a `0u` account id derived from an all-empty (or key-less, code-less) `UniversalStateInit` — whether by user error, a malicious front-end/dApp supplying a manipulated recipient address, or a phishing scheme that hands out a plausible-looking `0u...` string — permanently and irrecoverably loses those funds. There is no quorum, governance, or protocol-level mechanism (analogous to the missing `exitAdministrator`) to ever grant spending authority over such an account after the fact, since the account id is a pure hash commitment to a key set that was empty from the start. This is a concrete, permanent freezing of funds triggered entirely by ordinary, unprivileged transaction submission.

### Likelihood Explanation
Reachability requires only a standard `Transfer` action to a `0u`-format account id (no special privilege, RPC access is sufficient to construct and observe such ids via `derive_universal_account_id`/the `universal_state_init_to_account_id` host function), and optionally a follow-up `UniversalStateInit` action from any party to lock in the `Initialized` state. Because `0u` ids look like arbitrary opaque hex/base32-like strings, an attacker can trivially generate one with an empty state init and present it as a deposit/payment address without the victim being able to distinguish it from a normal, ownable universal account.

### Recommendation
Reject (at action-validation time) `UniversalStateInit` actions whose state init has **both** an empty `access_keys` set and no `code`/global-contract identifier, so that a definitively unspendable "burn" account can never be constructed and funded through the ordinary transaction path. If a genuine burn-address use case is desired, gate it behind an explicit, unambiguous action distinct from the general account-funding flow, so ordinary transfers cannot silently target it.

### Proof of Concept
1. Compute `state_init = UniversalStateInit::V1(UniversalStateInitV1 { code: None, data: BTreeMap::new(), access_keys: BTreeSet::new() })` and `account = state_init.derive_account_id()` (matches the "empty" fixture used in `runtime/near-vm-runner/src/logic/tests/miscs.rs:133-141`, producing a real, well-formed, 0u-prefixed account id).
2. Have Alice (an ordinary transaction signer) send `SignedTransaction::send_money(..., account, ...)`, creating an `Uninitialized` account holding her balance — exactly as in `test_universal_state_init_after_transfer` (`test-loop-tests/src/tests/universal_account_id.rs:343-395`), except here `state_init` (and thus `account`) commits to zero access keys.
3. Anyone (not necessarily Alice) submits `Action::UniversalStateInit { state_init: state_init.to_raw(), deposit: 0 }` targeting `account` — validated as `Ok(())` per `action_validation.rs:1598-1610` — which calls `action_universal_state_init`, moving the account to `Initialized` with no code and no access keys.
4. Alice's balance is now permanently held by an account with no signer and no contract; no subsequent transaction can ever move it, since no access key exists that the account id commits to and there is no protocol mechanism to grant one after the fact.

### Citations

**File:** core/primitives/src/utils.rs (L494-506)
```rust
// cspell:words UAID
/// Returns the `0u` universal account ID defined by `state_init`: SHA3-256
/// (FIPS-202) over exactly those bytes, encoded with the UAID address codec.
///
/// **Note:** This function deliberately does not take `UniversalStateInit`, but
/// `RawStateInit`, because account ID is committed to the exact user-supplied bytes.
/// Re-serializing could yield a different ID, if `state_init` contained non-canonical
/// borsh representation.
pub fn derive_universal_account_id(state_init: &RawStateInit) -> AccountId {
    use sha3::Digest;
    let hash = sha3::Sha3_256::digest(&state_init.0).into();
    encode_universal_account_id(&hash)
}
```

**File:** runtime/runtime/src/action_validation.rs (L1598-1610)
```rust
        // An init with neither code nor keys is not an error: it derives a single
        // well-defined account that nothing can act on, the `0u` equivalent of a
        // burn address.
        let empty = UniversalStateInit::V1(UniversalStateInitV1 {
            code: None,
            data: BTreeMap::new(),
            access_keys: BTreeSet::new(),
        });
        let empty_receiver = empty.derive_account_id();
        assert_eq!(
            validate_action(&limit, &action_for(&empty), &empty_receiver, feature_version),
            Ok(())
        );
```

**File:** runtime/runtime/src/universal_account_id.rs (L41-81)
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
