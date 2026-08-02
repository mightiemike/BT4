Confirmed: the asymmetry described in the exploit question exists in the current code.

### Title
Missing empty-key validation in `deserialize_multi_key` allows attacker-controlled zero-key MultiKey authentication material - (File: aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move)

### Summary
`new_multi_key_from_single_keys` enforces `num_keys > 0` before constructing a `MultiKey`, but the BCS deserialization path `deserialize_multi_key` (used by `new_public_key_from_bytes`, which is the function reachable from unprivileged transaction/account-rotation inputs) performs no such check.

### Finding Description
`new_multi_key_from_single_keys` explicitly asserts the key count invariant: [1](#0-0) 

However, `deserialize_multi_key`, which is the function invoked from raw, attacker-supplied bytes via `new_public_key_from_bytes`, builds the `MultiKey` struct directly from the BCS stream with **no** validation on the number of `public_keys` or on `signatures_required`: [2](#0-1) 

This means `new_public_key_from_bytes(bytes)` can succeed and return a `MultiKey { public_keys: vector[], signatures_required: 0 }` when the attacker supplies BCS bytes encoding a zero-length `public_keys` vector followed by any `signatures_required` byte (including 0), with no remaining bytes to trip the extra-bytes assertion. The resulting `to_authentication_key` function will then happily hash this degenerate struct into a 32-byte authentication key: [3](#0-2) 

The documented invariant in the module comment ("MultiKey public key is a collection of single key public keys") is violated for zero-key `MultiKey` values reachable through the bytes-deserialization entrypoint, while the "safe" constructor `new_multi_key_from_single_keys` correctly enforces it.

### Impact Explanation
If this degenerate zero-key, zero-threshold `MultiKey` public key is committed as an account's `authentication_key` (e.g., through the account key-rotation flow), the on-chain `Account.authentication_key` durable state would encode a signature scheme where "0 signatures out of 0 keys required" is interpreted as vacuously satisfied by verification logic that loops over the (empty) key set and checks that at least `signatures_required` of them signed. This corrupts the meaning of committed authentication-key state (a core piece of durable ledger data) and could allow an authentication check to be vacuously satisfied without any real key material — a state-integrity impact under the review's Required Impacts (committed state differing from correct/intended VM invariant, not merely a presentation issue).

I was not able to fully trace, within the available tool budget, the exact multi-key signature-verification code path in the Rust VM / transaction validation layer (i.e., whether the "k-out-of-n with k=0" case is actually accepted as a valid signature there, or whether an independent guard rejects `signatures_required == 0` or an empty bitmap at that layer). That verification would be necessary to confirm actual authentication bypass end-to-end versus only a Move-level invariant violation confined to `authentication_key` bytes that are otherwise inert.

### Likelihood Explanation
The path is fully reachable from unprivileged input: any account holder can call the public function `new_public_key_from_bytes` with hand-crafted BCS bytes (e.g., through the rotate-authentication-key transaction flow or any Move code calling this stdlib function), with no special privileges required.

### Recommendation
Add the same validation present in `new_multi_key_from_single_keys` (num_keys > 0, num_keys <= MAX_NUMBER_OF_PUBLIC_KEYS, signatures_required <= num_keys and > 0) directly inside `deserialize_multi_key`, so that all construction paths — both the explicit constructor and BCS deserialization — enforce identical invariants on the `MultiKey` struct.

### Proof of Concept
```move
// aptos-move/framework/aptos-stdlib/tests/cryptography/multi_key_tests.move (illustrative)
#[test]
fun test_empty_multi_key_deserializes_successfully() {
    // BCS encoding of: empty vector<AnyPublicKey> (len=0) + signatures_required=0
    let bytes = vector[0u8, 0u8];
    let pk = aptos_std::multi_key::new_public_key_from_bytes(bytes); // succeeds, no assertion failure
    // whereas the "safe" constructor rejects this:
    // multi_key::new_multi_key_from_single_keys(vector[], 0) aborts with E_INVALID_MULTI_KEY_NO_KEYS
}
``` [4](#0-3) 

Given the confirmed code-level asymmetry but the unverified downstream signature-verification enforcement, I present this as a valid Move-level invariant violation; full confirmation of a committed-state authentication bypass requires tracing the Rust-side multi-key signature verification logic, which was not accessible within this review's tool budget.

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
