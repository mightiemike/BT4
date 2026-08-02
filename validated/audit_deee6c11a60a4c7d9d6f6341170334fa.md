[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-stdlib/sources/cryptography/ristretto255_elgamal.move (L1-1)
```text

```

**File:** aptos-move/framework/aptos-framework/tests/confidential_asset/confidential_asset_tests.move (L686-709)
```text
        confidential_asset::deposit(&alice, token, 200);
        confidential_asset::rollover_pending_balance(&alice, token);

        withdraw(&alice, &alice_dk, token, bob_addr, 50, 150);

        // Must pause incoming transfers before key rotation (pending balance is already zero)
        confidential_asset::set_incoming_transfers_paused(&alice, token, true);

        let (new_alice_dk, new_alice_ek) = generate_twisted_elgamal_keypair();

        rotate(
            &alice,
            &alice_dk,
            token,
            &new_alice_dk,
        );

        assert!(confidential_asset::get_encryption_key(alice_addr, token) == new_alice_ek, 1);
        assert!(
            check_available_balance_decrypts_to(
                alice_addr, token, &new_alice_dk, 150, false
            ),
            1
        );
```
