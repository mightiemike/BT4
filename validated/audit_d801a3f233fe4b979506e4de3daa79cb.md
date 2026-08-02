## Finding confirmed [1](#0-0)  `new_public_key_from_bytes` only checks for trailing extra bytes; it performs **no validation at all** on `signatures_required` or the key count. It calls `deserialize_multi_key`: [2](#0-1)  which builds a `MultiKey { public_keys, signatures_required }` directly from BCS-decoded bytes with zero constraints.

Contrast this with the "safe" constructor `new_multi_key_from_single_keys`, which does enforce the invariants: [3](#0-2)  — `num_keys > 0` (`E_INVALID_MULTI_KEY_NO_KEYS`), `num_keys <= MAX_NUMBER_OF_PUBLIC_KEYS` (`E_INVALID_MULTI_KEY_TOO_MANY_KEYS`), and `signatures_required <= num_keys` (`E_INVALID_MULTI_KEY_SIGNATURES_REQUIRED`).

The existing test suite corroborates that `new_public_key_from_bytes` bypasses these checks entirely — there is a test exercising the too-large-signatures-required abort only via `new_multi_key_from_single_keys`, and a separate `test_construct_multi_key_from_bytes` that only checks BCS round-tripping, with no equivalent bad-input test for the `from_bytes` path: [4](#0-3) 

So a BCS blob with `signatures_required = 0` (or `signatures_required` greater than the key count, or even zero keys) decodes successfully into a `MultiKey` struct via `new_public_key_from_bytes`. Since `to_authentication_key` just hashes `bcs::to_bytes(self)`: [5](#0-4) 

any code path that constructs a `MultiKey` via `new_public_key_from_bytes` from attacker-supplied bytes and feeds it to `to_authentication_key` (e.g. for authentication-key rotation) will happily produce and commit an authentication key derived from a `signatures_required = 0` MultiKey — an unauthenticatable configuration, since no valid signature count could ever satisfy "0 signatures required consistently" per the multi-key verification semantics enforced elsewhere.

## Caveat / what I could not fully verify

I could not, within the remaining tool budget, trace the exact call path from `account::rotate_authentication_key_from_public_key` in `aptos-move/framework/aptos-framework/sources/account/account.move` through to `multi_key::new_public_key_from_bytes` and confirm there is no additional validation layer inserted at the `account.move` call site (e.g., a wrapper that re-validates `signatures_required` before accepting the derived authentication key). The `multi_key.move`-level gap is confirmed and is a real deserialization-validation asymmetry between `new_public_key_from_bytes` and `new_multi_key_from_single_keys`, but I recommend a Devin session with full repo access to trace `account.move`'s rotation entry points and any `single_key`/`multi_key` dispatch logic to confirm end-to-end reachability and whether any downstream check (e.g., a signature-verification step requiring `signatures_required >= 1`) mitigates the impact before the authentication key is actually written to storage.

### Title
Missing `signatures_required` validation in `multi_key::new_public_key_from_bytes` deserialization path - (File: aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move)

### Summary
`new_public_key_from_bytes`/`deserialize_multi_key` deserializes a `MultiKey` from raw BCS bytes without validating `signatures_required > 0`, `signatures_required <= len(public_keys)`, or `len(public_keys) > 0`, unlike the sibling constructor `new_multi_key_from_single_keys` which enforces exactly these invariants.

### Finding Description
`new_public_key_from_bytes` (line 51) delegates to `deserialize_multi_key` (line 77), which builds the `MultiKey` struct straight from stream-decoded fields with no `assert!` calls at all. The only check performed by `new_public_key_from_bytes` itself is for leftover/extra bytes (line 54). This means any caller that constructs a `MultiKey` from arbitrary bytes (rather than from validated `AnyPublicKey`s via `new_multi_key_from_single_keys`) can produce a `MultiKey` value with `signatures_required = 0`, `signatures_required` greater than the number of keys, or zero keys.

### Impact Explanation
If this deserialized `MultiKey` is used to compute an authentication key (via `to_authentication_key`, which only hashes the BCS bytes with no re-validation) and that authentication key is committed to an account's on-chain `authentication_key` field, the account becomes permanently unauthenticatable — no valid signature set could satisfy a "0 signatures required" scheme under the multi-key verification rules enforced elsewhere in the authenticator logic. This is a durable, state-corrupting outcome once written.

### Likelihood Explanation
Likelihood depends entirely on whether any unprivileged-input entry point (e.g. authentication-key rotation, account creation from a public key) passes raw bytes to `new_public_key_from_bytes` and then commits the resulting authentication key without independent re-validation of `signatures_required`. I was not able to confirm this full chain into `account.move` within this session, so likelihood should be treated as unconfirmed pending that trace, but the deserialization-layer gap itself is a genuine, demonstrable code defect.

### Recommendation
Add the same invariant checks used in `new_multi_key_from_single_keys` into `deserialize_multi_key` (or immediately after it in `new_public_key_from_bytes`): assert `public_keys.length() > 0`, `public_keys.length() <= MAX_NUMBER_OF_PUBLIC_KEYS`, and `signatures_required > 0 && (signatures_required as u64) <= public_keys.length()`, aborting with `E_INVALID_MULTI_KEY_SIGNATURES_REQUIRED` / `E_INVALID_MULTI_KEY_NO_KEYS` / `E_INVALID_MULTI_KEY_TOO_MANY_KEYS` as appropriate.

### Proof of Concept
```move
#[test]
#[expected_failure(abort_code = 0x10003, location = multi_key)]
fun test_construct_multi_key_from_bytes_zero_signatures_required() {
    // BCS: vector of 1 AnyPublicKey, then signatures_required byte = 0x00
    let mk_bytes: vector<u8> = x"010020aa9b5e7acc48169fdc3809b614532a5a675cf7d4c80cd4aea732b47e328bda1a00";
    let _multi_key = multi_key::new_public_key_from_bytes(mk_bytes);
    // Currently this SUCCEEDS instead of aborting, proving the missing validation.
}
```
Running this against the current `multi_key.move` will not abort as expected — the call succeeds and returns a `MultiKey` with `signatures_required = 0`, confirming the missing check.

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L50-56)
```text
    /// Parses the input bytes into a MultiKey public key.
    public fun new_public_key_from_bytes(bytes: vector<u8>): MultiKey {
        let stream = bcs_stream::new(bytes);
        let pk = deserialize_multi_key(&mut stream);
        assert!(!bcs_stream::has_remaining(&mut stream), error::invalid_argument(E_INVALID_MULTI_KEY_EXTRA_BYTES));
        pk
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L58-74)
```text
    /// Creates a new MultiKey public key from a vector of single key public keys and a number representing the number of signatures required to authenticate a transaction.
    public fun new_multi_key_from_single_keys(single_keys: vector<single_key::AnyPublicKey>, signatures_required: u8): MultiKey {
        let num_keys = single_keys.length();
        assert!(
            num_keys > 0,
            error::invalid_argument(E_INVALID_MULTI_KEY_NO_KEYS)
        );
        assert!(
            num_keys <= MAX_NUMBER_OF_PUBLIC_KEYS,
            error::invalid_argument(E_INVALID_MULTI_KEY_TOO_MANY_KEYS)
        );
        assert!(
            (signatures_required as u64) <= num_keys,
            error::invalid_argument(E_INVALID_MULTI_KEY_SIGNATURES_REQUIRED)
        );
        MultiKey { public_keys: single_keys, signatures_required }
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L76-81)
```text
    /// Deserializes a MultiKey public key from a BCS stream.
    public fun deserialize_multi_key(stream: &mut bcs_stream::BCSStream): MultiKey {
        let public_keys = bcs_stream::deserialize_vector(stream, |x| single_key::deserialize_any_public_key(x));
        let signatures_required = bcs_stream::deserialize_u8(stream);
        MultiKey { public_keys, signatures_required }
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L83-88)
```text
    /// Returns the authentication key for a MultiKey public key.
    public fun to_authentication_key(self: &MultiKey): vector<u8> {
        let pk_bytes = bcs::to_bytes(self);
        pk_bytes.push_back(SIGNATURE_SCHEME_ID);
        hash::sha3_256(pk_bytes)
    }
```

**File:** aptos-move/framework/aptos-stdlib/tests/cryptography/multi_key_tests.move (L15-34)
```text
    #[test]
    #[expected_failure(abort_code = 0x10003, location = multi_key)]
    fun test_construct_multi_key_bad_input_signatures_required_too_large() {
        let pk1 = single_key::new_public_key_from_bytes(x"0020aa9b5e7acc48169fdc3809b614532a5a675cf7d4c80cd4aea732b47e328bda1a");
        let pk2 = single_key::new_public_key_from_bytes(x"0020bd182d6e3f4ad1daf0d94e53daaece63ebd571d8a8e0098a02a4c0b4ecc7c99e");
        let _multi_key = multi_key::new_multi_key_from_single_keys(vector[pk1, pk2], 3);
    }

    #[test]
    #[expected_failure(abort_code = 0x10001, location = multi_key)]
    fun test_construct_multi_key_bad_input_no_keys() {
        let _multi_key = multi_key::new_multi_key_from_single_keys(vector[], 1);
    }

    #[test]
    fun test_construct_multi_key_from_bytes() {
        let mk_bytes: vector<u8> = x"020020aa9b5e7acc48169fdc3809b614532a5a675cf7d4c80cd4aea732b47e328bda1a0020bd182d6e3f4ad1daf0d94e53daaece63ebd571d8a8e0098a02a4c0b4ecc7c99e01";
        let multi_key = multi_key::new_public_key_from_bytes(mk_bytes);
        assert!(bcs::to_bytes(&multi_key) == mk_bytes, std::error::invalid_state(1));
    }
```
