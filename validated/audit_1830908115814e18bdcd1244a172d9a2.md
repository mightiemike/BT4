## Finding: Missing threshold validation in `deserialize_multi_key` bypasses the check enforced in `new_multi_key_from_single_keys`

### Summary
The `multi_key` module contains two different construction paths for a `MultiKey` struct, and only one of them enforces the invariant that `signatures_required <= public_keys.length()`.

### Finding Description
`new_multi_key_from_single_keys` explicitly validates the threshold: [1](#0-0) 

But `deserialize_multi_key` — the function invoked by the public, unprivileged entry point `new_public_key_from_bytes` — constructs the same struct directly from raw BCS bytes with **no equivalent check**: [2](#0-1) 

Any caller who supplies bytes via `new_public_key_from_bytes` (e.g. `x"...02" ` where `signatures_required=2` but only 1 key is encoded) obtains a `MultiKey { public_keys, signatures_required }` value where `signatures_required > public_keys.length()`, and the only guard against extra bytes is `E_INVALID_MULTI_KEY_EXTRA_BYTES` (unrelated to the threshold). `to_authentication_key` then happily BCS-serializes and hashes this malformed struct into a 32-byte authentication key with no validation at all: [3](#0-2) 

The existing test suite only exercises the invalid-threshold abort against `new_multi_key_from_single_keys`, never against the BCS-deserialization path, which is consistent with this gap being unexamined: [4](#0-3) 

### Impact Explanation
This is a genuine internal inconsistency in the Move module: `deserialize_multi_key`/`new_public_key_from_bytes` do not enforce the same invariant that the "canonical" constructor enforces, so a `MultiKey` value that can never be satisfied by any signature set can be built and hashed into an authentication key at the Move layer.

However, I was **not able to confirm within this review** that this Move-level struct is what actually governs authentication-key commitment/verification for real transactions. Key open points:
- `aptos-move/framework/aptos-framework/sources/account/account.move` does not reference the `multi_key` module by name at all (0 matches for the module identifier), even though it does reference `new_public_key_from_bytes`-named functions — those matches appear to belong to other key modules (e.g. ed25519/multi_ed25519), not `multi_key`.
- The actual signature-count-vs-threshold enforcement for "MultiKeySignature" transaction authenticators is implemented independently in Rust (`types/src/transaction/authenticator.rs`), and I could not verify in the time available whether that Rust-side parser reuses/duplicates this exact Move-level check, is independently strict, or is likewise permissive.
- The on-chain `AuthenticationKey` resource stores only the 32-byte hash, not the struct; whether an "unsatisfiable" hash can actually be committed as an account's controlling key depends on the rotation-proof flow (`rotate_authentication_key`) accepting such a struct/hash, which I could not fully trace within this review's tool budget.

Without confirming that path end-to-end, I cannot assert this produces a mainnet-committed-state corruption per the review's State-Integrity Gate — it may be that Rust-side authenticator logic independently and correctly rejects such structs at signature-verification time, in which case the impact is limited to an internal Move-code inconsistency rather than an exploitable ledger-state bug.

### Likelihood Explanation
Trivial to trigger from unprivileged Move code or a client script: anyone can call `aptos_std::multi_key::new_public_key_from_bytes` with hand-crafted BCS bytes encoding `signatures_required > public_keys.length()`.

### Recommendation
Add the same threshold assertion used in `new_multi_key_from_single_keys` to `deserialize_multi_key`, e.g.:
```
assert!(
    (signatures_required as u64) <= public_keys.length(),
    error::invalid_argument(E_INVALID_MULTI_KEY_SIGNATURES_REQUIRED)
);
```
placed inside `deserialize_multi_key` before constructing the struct, so both construction paths enforce the identical invariant. Additionally, verify (in a follow-up review with terminal/repo access) whether Rust-side `MultiKeySignature`/`MultiKeyAuthenticator` logic in `types/src/transaction/authenticator.rs` independently re-validates this threshold before accepting a submitted authenticator, and whether `rotate_authentication_key`/account-creation flows call this Move deserializer on attacker-supplied bytes before committing an authentication key.

### Proof of Concept
```move
#[test]
fun test_deserialize_multi_key_bypasses_threshold_check() {
    // 1 key, but signatures_required = 2 encoded in the trailing byte
    let pk1 = single_key::new_public_key_from_bytes(x"0020aa9b5e7acc48169fdc3809b614532a5a675cf7d4c80cd4aea732b47e328bda1a");
    let bad_bytes = /* BCS: vector[pk1] followed by u8 = 2 */;
    // Does NOT abort with E_INVALID_MULTI_KEY_SIGNATURES_REQUIRED, unlike
    // multi_key::new_multi_key_from_single_keys(vector[pk1], 2)
    let mk = multi_key::new_public_key_from_bytes(bad_bytes);
    // mk.signatures_required (2) > mk.public_keys.length() (1)
}
```

**Note:** I am flagging this as a confirmed Move-level logic inconsistency, but I could not fully verify with certainty (given tool-call limits) whether this gap is actually reachable to corrupt a *committed* authentication key that later prevents valid transaction authorization on mainnet, versus being isolated to an internal helper whose output is independently re-validated by Rust-side signature verification. A full confirmation would require tracing `rotate_authentication_key`/account creation flows and the Rust `MultiKeySignature` verifier, which needs a Devin session with repo/terminal access to complete.

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L50-81)
```text
    /// Parses the input bytes into a MultiKey public key.
    public fun new_public_key_from_bytes(bytes: vector<u8>): MultiKey {
        let stream = bcs_stream::new(bytes);
        let pk = deserialize_multi_key(&mut stream);
        assert!(!bcs_stream::has_remaining(&mut stream), error::invalid_argument(E_INVALID_MULTI_KEY_EXTRA_BYTES));
        pk
    }

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
