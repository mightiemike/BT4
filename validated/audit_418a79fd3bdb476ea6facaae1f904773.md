[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L17-32)
```text
/// create_with_owners where multiple initial owner addresses can be specified. This is different (and easier) from
/// the native multisig scheme where the owners' public keys have to be specified. Here, only addresses are needed.
/// 2. Owners can be added/removed any time by calling add_owners or remove_owners. The transactions to do still need
/// to follow the k-of-n scheme specified for the multisig account.
/// 3. To create a new transaction, an owner can call create_transaction with the transaction payload. This will store
/// the full transaction payload on chain, which adds decentralization (censorship is not possible as the data is
/// available on chain) and makes it easier to fetch all transactions waiting for execution. If saving gas is desired,
/// an owner can alternatively call create_transaction_with_hash where only the payload hash is stored. Later execution
/// will be verified using the hash. Only owners can create transactions and a transaction id (incremeting id) will be
/// assigned.
/// 4. To approve or reject a transaction, other owners can call approve() or reject() with the transaction id.
/// 5. If there are enough approvals, any owner can execute the transaction using the special MultisigTransaction type
/// with the transaction id if the full payload is already stored on chain or with the transaction payload if only a
/// hash is stored. Transaction execution will first check with this module that the transaction payload has gotten
/// enough signatures. If so, it will be executed as the multisig account. The owner who executes will pay for gas.
/// 6. If there are enough rejections, any owner can finalize the rejection by calling execute_rejected_transaction().
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L38-55)
```text
module aptos_framework::multisig_account {
    use aptos_framework::account::{Self, SignerCapability, new_event_handle, create_resource_address};
    use aptos_framework::aptos_coin::AptosCoin;
    use aptos_framework::chain_id;
    use aptos_framework::create_signer::create_signer;
    use aptos_framework::coin;
    use aptos_framework::event::{EventHandle, emit};
    use aptos_framework::timestamp::now_seconds;
    use aptos_std::simple_map::{Self, SimpleMap};
    use aptos_std::table::{Self, Table};
    use std::bcs::to_bytes;
    use std::features;
    use std::error;
    use std::hash::sha3_256;
    use std::option::{Self, Option};
    use std::signer::address_of;
    use std::string::String;
    use std::vector;
```
