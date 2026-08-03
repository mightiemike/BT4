## Finding Confirmed

The code confirms the exact bypass described: `deserialize_multi_key` in `multi_key.move` does not enforce the no-empty-keys invariant that `new_multi_key_from_single_keys` enforces.

### Title
Missing empty-key validation in `multi_key::deserialize_multi_key` allows an unsatisfiable authentication key to be committed to Account state - (File: `aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move`)

### Summary
`new_multi_key_from_single_keys` explicitly rejects zero-length key vectors via `E_INVALID_MULTI_KEY_NO_KEYS`: [1](#0-0) 

But `deserialize_multi_key`, used by the public constructor `new_public_key_from_bytes`, performs no such check — it simply BCS-decodes a vector of any length (including zero) and a threshold byte: [2](#0-1) 

### Finding Description
`to_authentication_key` unconditionally hashes the BCS bytes of the `MultiKey` struct regardless of whether `public_keys` is empty, always returning a well-formed 32-byte digest: [3](#0-2) 

This is reachable from an unprivileged entry function. In `account.move`, `rotate_authentication_key_from_public_key` takes raw `new_public_key_bytes` from the transaction sender and, for `scheme == MULTI_KEY_SCHEME`, calls `multi_key::new_public_key_from_bytes(...).to_authentication_key()` with no key-count validation: [4](#0-3) 

The resulting 32-byte hash is then committed to the `Account.authentication_key` state field by `rotate_authentication_key_internal`, whose only guard is a length check (`== 32`), not a scheme-specific structural check: [5](#0-4) 

Once committed, the resulting authentication key can never be matched by a valid signature. On the verification side, `MultiKeyAuthenticator::to_single_key_authenticators` requires `signatures_bitmap.last_set_bit().is_some()` and any signature index to be `< public_keys.len()`; with an empty `public_keys` vector this is unsatisfiable, so no `AccountAuthenticator::MultiKey` can ever verify against this auth key: [6](#0-5) 

### Impact Explanation
The Account resource's `authentication_key` field — a value committed into on-chain state — can be set to a hash that structurally corresponds to zero keys, meaning no signer can ever again produce a valid transaction authenticator for that account. This is a genuine violation of the stated invariant ("an authentication mechanism committed to state must reference at least one key"), and the corruption occurs purely from unprivileged, attacker/user-supplied transaction bytes with no operator error involved.

However, note the trigger path requires the account owner (the `&signer` of `rotate_authentication_key_from_public_key`) to submit the malformed bytes for their *own* account — this function only rotates the caller's own key and cannot be used to corrupt a third party's account. The realistic impact is therefore a self-inflicted, permanent account lockout, not an attacker corrupting another user's committed state. No cross-account griefing path was found: `rotate_authentication_key` requires a valid PoK signature under the new key material (which is impossible to produce for zero keys, since `MultiKeyAuthenticator` verification is unsatisfiable), and `rotate_authentication_key_with_rotation_capability` requires the offerer to have granted rotation capability to the delegate.

### Likelihood Explanation
Trivial to trigger for anyone who wants to permanently disable their own account (a self-inflicted footgun) via a single unprivileged `rotate_authentication_key_from_public_key` entry-function call. No special privileges or protocol/consensus manipulation required.

### Recommendation
Add the same `public_keys.length() > 0` (and `<= MAX_NUMBER_OF_PUBLIC_KEYS`) assertions inside `deserialize_multi_key`, or explicitly re-validate the deserialized `MultiKey` in `new_public_key_from_bytes` before returning, mirroring the checks already present in `new_multi_key_from_single_keys`.

### Proof of Concept
A Move unit test analogous to the existing `multi_key_tests.move` suite: [7](#0-6) 
```move
#[test]
fun test_deserialize_multi_key_zero_keys_does_not_abort() {
    // BCS-encoded: empty vector (ULEB128 length 0) + threshold byte 0x01
    let mk_bytes: vector<u8> = x"0001";
    let multi_key = multi_key::new_public_key_from_bytes(mk_bytes); // does NOT abort
    let _auth_key = multi_key.to_authentication_key(); // returns a well-formed 32-byte hash
}
```
Contrast with:
```move
#[test]
#[expected_failure(abort_code = 0x10001, location = multi_key)]
fun test_construct_multi_key_bad_input_no_keys() {
    let _multi_key = multi_key::new_multi_key_from_single_keys(vector[], 1); // aborts
}
```
demonstrating the asymmetric validation between the two constructors, and that the unchecked path is directly reachable from the unprivileged `rotate_authentication_key_from_public_key` entry function.

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

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L439-448)
```text
    public(friend) fun rotate_authentication_key_internal(account: &signer, new_auth_key: vector<u8>) acquires Account {
        let addr = signer::address_of(account);
        ensure_resource_exists(addr);
        assert!(
            new_auth_key.length() == 32,
            error::invalid_argument(EMALFORMED_AUTHENTICATION_KEY)
        );
        let account_resource = &mut Account[addr];
        account_resource.authentication_key = new_auth_key;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L462-494)
```text
    /// Private entry function for key rotation that allows the signer to update their authentication key from a given public key.
    /// This function will abort if the scheme is not recognized or if new_public_key_bytes is not a valid public key for the given scheme.
    ///
    /// Note: This function does not update the `OriginatingAddress` table.
    entry fun rotate_authentication_key_from_public_key(account: &signer, scheme: u8, new_public_key_bytes: vector<u8>) acquires Account {
        let addr = signer::address_of(account);
        let account_resource = &Account[addr];
        let old_auth_key = account_resource.authentication_key;
        let new_auth_key;
        if (scheme == ED25519_SCHEME) {
            let from_pk = ed25519::new_unvalidated_public_key_from_bytes(new_public_key_bytes);
            new_auth_key = ed25519::unvalidated_public_key_to_authentication_key(&from_pk);
        } else if (scheme == MULTI_ED25519_SCHEME) {
            let from_pk = multi_ed25519::new_unvalidated_public_key_from_bytes(new_public_key_bytes);
            new_auth_key = multi_ed25519::unvalidated_public_key_to_authentication_key(&from_pk);
        } else if (scheme == SINGLE_KEY_SCHEME) {
            new_auth_key = single_key::new_public_key_from_bytes(new_public_key_bytes).to_authentication_key();
        } else if (scheme == MULTI_KEY_SCHEME) {
            new_auth_key = multi_key::new_public_key_from_bytes(new_public_key_bytes).to_authentication_key();
        } else {
            abort error::invalid_argument(EUNRECOGNIZED_SCHEME)
        };
        rotate_authentication_key_call(account, new_auth_key);
        event::emit(KeyRotationToPublicKey {
            account: addr,
            // Set verified_public_key_bit_map to [0x00, 0x00, 0x00, 0x00] as the public key(s) are not verified
            verified_public_key_bit_map: vector[0x00, 0x00, 0x00, 0x00],
            public_key_scheme: scheme,
            public_key: new_public_key_bytes,
            old_auth_key,
            new_auth_key,
        });
    }
```

**File:** types/src/transaction/authenticator.rs (L1167-1199)
```rust
    pub fn to_single_key_authenticators(&self) -> Result<Vec<SingleKeyAuthenticator>> {
        ensure!(
            self.signatures_bitmap.last_set_bit().is_some(),
            "There were no signatures set in the bitmap."
        );

        ensure!(
            (self.signatures_bitmap.last_set_bit().unwrap() as usize) < self.public_keys.len(),
            "Mismatch in the position of the last signature and the number of PKs, {} >= {}.",
            self.signatures_bitmap.last_set_bit().unwrap(),
            self.public_keys.len(),
        );
        ensure!(
            self.signatures_bitmap.count_ones() as usize == self.signatures.len(),
            "Mismatch in number of signatures and the number of bits set in the signatures_bitmap, {} != {}.",
            self.signatures_bitmap.count_ones(),
            self.signatures.len(),
        );
        ensure!(
            self.signatures.len() >= self.public_keys.signatures_required() as usize,
            "Not enough signatures for verification, {} < {}.",
            self.signatures.len(),
            self.public_keys.signatures_required(),
        );
        let authenticators: Vec<SingleKeyAuthenticator> =
            std::iter::zip(self.signatures_bitmap.iter_ones(), self.signatures.iter())
                .map(|(idx, sig)| SingleKeyAuthenticator {
                    public_key: self.public_keys.public_keys[idx].clone(),
                    signature: sig.clone(),
                })
                .collect();
        Ok(authenticators)
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
