Confirmed and directly reproduced in the codebase's own test suite: `mk_auth_12` in `verify_multi_key_auth` (types/src/transaction/authenticator.rs:1920-1932) demonstrates the exact behavior the exploit question describes.

### Title
`MultiKey` accepts duplicate public keys, letting `signatures_required` be trivially satisfied by a single distinct signer - (File: `aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move`)

### Summary
`new_multi_key_from_single_keys` only checks `signatures_required <= num_keys` (count of vector entries) with no uniqueness constraint on `public_keys`. [1](#0-0)  The Rust-side `MultiKey::new` used to build the on-chain authentication key enforces the identical, count-only check. [2](#0-1)  Neither layer deduplicates `public_keys`, so `signatures_required` is validated against the number of *slots*, not the number of *independent signers*.

### Finding Description
An unprivileged actor can construct a `MultiKey`/`MultiKeyAuthenticator` such as `public_keys = [pk_A, pk_B, pk_B]` with `signatures_required = 2`. Both indices 1 and 2 reference the identical key `pk_B`, so a signature produced once by the holder of `pk_B` is valid for both indices, letting the "2-of-3" threshold be satisfied with signatures from only 2 distinct keys where one of them (`pk_B`) is reused — the bitmap/index-based verification in `MultiKeyAuthenticator::new` and `to_single_key_authenticators` only rejects duplicate *indices*, not duplicate *underlying keys*. [3](#0-2) [4](#0-3) 

The framework's own regression test builds exactly this authenticator: `keys = [any_sender0_pub, any_sender1_pub, any_sender1_pub]`, `multi_key = MultiKey::new(keys, 2)`, then submits `mk_auth_12` = signatures at indices 1 and 2, both using `signature1` (the single signature of `sender1`). The resulting authenticator passes `to_single_key_authenticators` and `signed_txn.verify_signature().unwrap()` succeeds. [5](#0-4)  This is a state-committed authentication key (derived via `to_authentication_key` / `AuthenticationKey::multi_key`) whose declared `signatures_required = 2` is satisfiable with only one distinct private key (`sender1`), not two.

### Impact Explanation
The persisted `signatures_required` field no longer represents an accurate threshold of independent signers once an account rotates its auth key to such a `MultiKey`. Anyone who can construct such a key (at account creation or via key rotation, both unprivileged operations) can create an account resource whose on-chain authenticator claims "k-of-n" security but is satisfiable by fewer distinct signers than `k`, or in more extreme padding (all `n` entries identical) becomes fully satisfiable by a single key holder regardless of the recorded `k`. This corrupts the semantic guarantee of the persisted authenticator state without any bytecode bug in the signature/bitmap verification itself — the check is index-uniqueness, not key-uniqueness. It does not, however, forge signatures or bypass cryptographic verification for any single key; each index must still be validly signed. The impact is a weakened/misleading threshold guarantee on the affected account's own authenticator, not a cross-account state corruption.

### Likelihood Explanation
High: constructing a `MultiKey` with duplicate `AnyPublicKey` entries requires no special privilege — it's done entirely with `new_multi_key_from_single_keys`/`new_public_key_from_bytes` (Move) or `MultiKey::new` (Rust authenticator construction), both reachable from ordinary account creation and authentication-key-rotation flows. The framework's own unit test proves this construction and its acceptance by `verify_signature()`.

### Recommendation
Enforce uniqueness of `public_keys` entries in both `new_multi_key_from_single_keys` (Move) and `MultiKey::new` (Rust), rejecting duplicate keys (or bytes-equal serialized keys) at construction time so `signatures_required` always reflects the number of distinct signers needed.

### Proof of Concept
Reference the existing test `verify_multi_key_auth` in `types/src/transaction/authenticator.rs`: build `MultiKey::new(vec![pk_A, pk_B, pk_B], 2)`, then `MultiKeyAuthenticator::new(multi_key, vec![(1, sig_B), (2, sig_B)])` and observe `signed_txn.verify_signature()` returns `Ok(())` despite only one distinct private key (`sender1`) having produced any signature. [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/multi_key.move (L59-74)
```text
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

**File:** types/src/transaction/authenticator.rs (L1120-1151)
```rust
    pub fn new(public_keys: MultiKey, signatures: Vec<(u8, AnySignature)>) -> Result<Self> {
        ensure!(
            public_keys.len() < (u8::MAX as usize),
            "Too many public keys, {}, in MultiKeyAuthenticator.",
            public_keys.len(),
        );

        let mut signatures_bitmap = aptos_bitvec::BitVec::with_num_bits(public_keys.len() as u16);
        let mut any_signatures = vec![];

        for (idx, signature) in signatures {
            ensure!(
                (idx as usize) < public_keys.len(),
                "Signature index is out of public key range, {} < {}.",
                idx,
                public_keys.len(),
            );
            ensure!(
                !signatures_bitmap.is_set(idx as u16),
                "Duplicate signature index, {}.",
                idx
            );
            signatures_bitmap.set(idx as u16);
            any_signatures.push(signature);
        }

        Ok(MultiKeyAuthenticator {
            public_keys,
            signatures: any_signatures,
            signatures_bitmap,
        })
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

**File:** types/src/transaction/authenticator.rs (L1241-1264)
```rust
    pub fn new(public_keys: Vec<AnyPublicKey>, signatures_required: u8) -> Result<Self> {
        ensure!(
            signatures_required > 0,
            "The number of required signatures is 0."
        );

        ensure!(
            public_keys.len() <= MAX_NUM_OF_SIGS, // This max number of signatures is also the max number of public keys.
            "The number of public keys is greater than {}.",
            MAX_NUM_OF_SIGS
        );

        ensure!(
            public_keys.len() >= signatures_required as usize,
            "The number of public keys is smaller than the number of required signatures, {} < {}",
            public_keys.len(),
            signatures_required
        );

        Ok(Self {
            public_keys,
            signatures_required,
        })
    }
```

**File:** types/src/transaction/authenticator.rs (L1845-1932)
```rust
        let keys = vec![
            any_sender0_pub.clone(),
            any_sender1_pub.clone(),
            any_sender1_pub.clone(),
        ];
        let multi_key = MultiKey::new(keys, 2).unwrap();

        let sender_auth = AuthenticationKey::multi_key(multi_key.clone());
        let sender_addr = sender_auth.account_address();

        let raw_txn = crate::test_helpers::transaction_test_helpers::get_test_signed_transaction(
            sender_addr,
            0,
            &sender0,
            sender0_pub,
            None,
            0,
            0,
            None,
        )
        .into_raw_transaction();

        let signature0 = AnySignature::ed25519(sender0.sign(&raw_txn).unwrap());
        let sender0_auth = SingleKeyAuthenticator {
            public_key: any_sender0_pub,
            signature: signature0.clone(),
        };
        let signature1 = AnySignature::secp256k1_ecdsa(sender1.sign(&raw_txn).unwrap());
        let sender1_auth = SingleKeyAuthenticator {
            public_key: any_sender1_pub,
            signature: signature1.clone(),
        };

        let mk_auth_0 =
            MultiKeyAuthenticator::new(multi_key.clone(), vec![(0, signature0.clone())]).unwrap();
        mk_auth_0.to_single_key_authenticators().unwrap_err();
        let account_auth = AccountAuthenticator::multi_key(mk_auth_0);
        let signed_txn = SignedTransaction::new_single_sender(raw_txn.clone(), account_auth);
        signed_txn.verify_signature().unwrap_err();

        let mk_auth_1 =
            MultiKeyAuthenticator::new(multi_key.clone(), vec![(1, signature1.clone())]).unwrap();
        mk_auth_1.to_single_key_authenticators().unwrap_err();
        let account_auth = AccountAuthenticator::multi_key(mk_auth_1);
        let signed_txn = SignedTransaction::new_single_sender(raw_txn.clone(), account_auth);
        signed_txn.verify_signature().unwrap_err();

        let mk_auth_01 = MultiKeyAuthenticator::new(multi_key.clone(), vec![
            (0, signature0.clone()),
            (1, signature1.clone()),
        ])
        .unwrap();
        let single_key_authenticators = mk_auth_01.to_single_key_authenticators().unwrap();
        assert_eq!(single_key_authenticators, vec![
            sender0_auth.clone(),
            sender1_auth.clone()
        ]);
        let account_auth = AccountAuthenticator::multi_key(mk_auth_01);
        let signed_txn = SignedTransaction::new_single_sender(raw_txn.clone(), account_auth);
        signed_txn.verify_signature().unwrap();

        let mk_auth_02 = MultiKeyAuthenticator::new(multi_key.clone(), vec![
            (0, signature0.clone()),
            (2, signature1.clone()),
        ])
        .unwrap();
        let single_key_authenticators = mk_auth_02.to_single_key_authenticators().unwrap();
        assert_eq!(single_key_authenticators, vec![
            sender0_auth.clone(),
            sender1_auth.clone()
        ]);
        let account_auth = AccountAuthenticator::multi_key(mk_auth_02);
        let signed_txn = SignedTransaction::new_single_sender(raw_txn.clone(), account_auth);
        signed_txn.verify_signature().unwrap();

        let mk_auth_12 = MultiKeyAuthenticator::new(multi_key.clone(), vec![
            (1, signature1.clone()),
            (2, signature1.clone()),
        ])
        .unwrap();
        let single_key_authenticators = mk_auth_12.to_single_key_authenticators().unwrap();
        assert_eq!(single_key_authenticators, vec![
            sender1_auth.clone(),
            sender1_auth.clone()
        ]);
        let account_auth = AccountAuthenticator::multi_key(mk_auth_12);
        let signed_txn = SignedTransaction::new_single_sender(raw_txn.clone(), account_auth);
        signed_txn.verify_signature().unwrap();
```
