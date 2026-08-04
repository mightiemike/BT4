[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/primary_fungible_store.move (L215-235)
```text
    /// Transfer `amount` of fungible asset from sender's primary store to receiver's primary store.
    /// Use the minimum deposit assertion api to make sure receipient will receive a minimum amount of fund.
    public entry fun transfer_assert_minimum_deposit<T: key>(
        sender: &signer,
        metadata: Object<T>,
        recipient: address,
        amount: u64,
        expected: u64,
    ) acquires DeriveRefPod {
        let sender_store = ensure_primary_store_exists(signer::address_of(sender), metadata);
        // Check if the sender store object has been burnt or not. If so, unburn it first.
        may_be_unburn(sender, sender_store);
        let recipient_store = ensure_primary_store_exists(recipient, metadata);
        dispatchable_fungible_asset::transfer_assert_minimum_deposit(
            sender,
            sender_store,
            recipient_store,
            amount,
            expected
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/primary_fungible_store.move (L282-286)
```text
    fun may_be_unburn(owner: &signer, store: Object<FungibleStore>) {
        if (store.is_burnt()) {
            object::unburn(owner, store);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/primary_fungible_store.move (L384-406)
```text
    #[test(user_1 = @0xcafe, user_2 = @0xface)]
    fun test_transfer_to_burnt_store(
        user_1: &signer,
        user_2: &signer,
    ) acquires DeriveRefPod {
        let (creator_ref, metadata) = create_test_token(user_1);
        let (mint_ref, _, _) = init_test_metadata_with_primary_store_enabled(&creator_ref);
        let user_1_address = signer::address_of(user_1);
        let user_2_address = signer::address_of(user_2);
        mint(&mint_ref, user_1_address, 100);
        transfer(user_1, metadata, user_2_address, 80);

        // User 2 burns their primary store but should still be able to transfer afterward.
        let user_2_primary_store = primary_store(user_2_address, metadata);
        object::burn_object_with_transfer(user_2, user_2_primary_store);
        assert!(user_2_primary_store.is_burnt(), 0);
        // Balance still works
        assert!(balance(user_2_address, metadata) == 80, 0);
        // Deposit still works
        transfer(user_1, metadata, user_2_address, 20);
        transfer(user_2, metadata, user_1_address, 90);
        assert!(balance(user_2_address, metadata) == 10, 0);
    }
```
