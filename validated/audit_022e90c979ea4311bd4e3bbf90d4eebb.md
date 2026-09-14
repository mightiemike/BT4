### Title
ERC-20 emulation in the NEAR Wallet Contract sends tokens to an unchecked ETH-implicit account derived from a raw Ethereum `to` address, permanently losing funds sent to non-EOA (smart-contract-controlled) addresses - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs`)

### Summary
The NEAR Wallet Contract's ERC-20 `transfer` emulation converts the ABI-encoded Ethereum `to` address directly into an ETH-implicit NEAR account ID and executes `ft_transfer` to it, without any lookup in the Address Registrar or any validation that the target address is actually controlled by a discoverable secp256k1 private key. If the Ethereum `to` address belongs to a smart-contract wallet (e.g. a Gnosis Safe/CREATE2-deployed contract with no single private key), the resulting NEAR account can never be controlled, and the transferred tokens are permanently lost — the same root cause as the referenced Ondo bridge bug: assuming address equivalence/ownership continuity across two systems with fundamentally different account-control mechanisms.

### Finding Description
The Wallet Contract (NEP-518) lets Ethereum-style EOA users interact with NEAR by wrapping RLP-encoded Ethereum transactions and executing them as NEAR actions from an "ETH-implicit account," whose account ID is `'0x' + keccak256(pubkey)[12:32].hex()` and which can *only* be operated by the holder of the matching secp256k1 private key [1](#0-0) .

When the Wallet Contract emulates an ERC-20 `transfer(address,uint256)` call, it decodes the raw 20-byte `to` address from the calldata and constructs the NEAR `receiver_id` by directly re-encoding that address as `0x{hex}{suffix}`, then issues an `ft_transfer` to it: [2](#0-1) 

This is fundamentally different from how the same contract handles a plain native-token ("base token") transfer to another eth-implicit target: for that case, `validate_tx_relayer_data`/`parse_target` explicitly require checking the Address Registrar (`EOABaseTokenTransfer { address_check: Some(address), .. }`) before finalizing the target, specifically to catch a "lazy"/faulty relayer that sent funds to an address without confirming there's no named account behind it: [3](#0-2) [4](#0-3) 

The Address Registrar (`AddressRegistrar::register`) exists exactly to let a real NEAR account "claim" the address that `keccak256(account_id)` maps to, so relayers/users can route funds to the intended named account instead of a semantically-meaningless raw address: [5](#0-4) 

However, the ERC-20 `transfer` path never consults this registrar for the `to` address — it unconditionally treats the raw Ethereum address as an ETH-implicit NEAR account ID [6](#0-5) . This mirrors exactly the Ondo `SourceBridge.burnAndCallAxelar` flaw: `msg.sender`/the source-chain address is blindly reused as the destination identity (`abi.encode(VERSION, msg.sender, amount, nonce++)`), assuming the same address is controlled by the same party on the other side, when in fact "the same address" can be controlled by fundamentally different mechanisms (EOA private key vs. smart-contract/multisig logic) on the two sides.

### Impact Explanation
Any user (or dApp) interacting with a NEP-141 token through the Wallet Contract's ERC-20 emulation who sends tokens to a smart-contract-wallet address (e.g., a Safe/Gnosis multisig address, or any Ethereum address that is not an EOA backed by a single secp256k1 key) will have those tokens irrecoverably locked in a NEAR ETH-implicit account: per the protocol, that account can only ever be operated via `rlp_execute` after verifying a signature from the corresponding secp256k1 private key, which does not exist for a smart-contract-controlled address [7](#0-6) . This constitutes permanently frozen funds — no future action can rescue the balance, since ETH-implicit accounts cannot receive a full access key or be deleted [8](#0-7) .

### Likelihood Explanation
The trigger requires only an ordinary unprivileged user (or dApp/relayer acting on the user's behalf) constructing a standard Ethereum ERC-20 `transfer` transaction targeting an address they control on Ethereum through non-EOA means (increasingly common given the popularity of smart-contract/multisig wallets), then submitting it through any relayer to `rlp_execute`. No special privileges, validator collusion, or malicious relayer behavior is needed — this is exactly the intended "happy path" of the ERC-20 emulation feature, and the bug is a missing validation/registrar-check that exists for the analogous native-transfer case but was omitted for ERC-20 transfers.

### Recommendation
For the ERC-20 `transfer` (and any similar NEP-141 emulation) path, apply the same registrar-based safety check already used for native-token transfers: before finalizing `receiver_id` as a raw ETH-implicit account derived from the `to` address, query the Address Registrar for a named account claiming that address, and use it if present; additionally, expose clear documentation/warnings (and ideally an on-chain check or opt-in confirmation) that ERC-20 transfers to unregistered eth-implicit addresses assume the destination is a plain EOA, and that funds sent to smart-contract-wallet addresses without a corresponding secp256k1 key are unrecoverable.

### Proof of Concept
1. Deploy a NEP-141 token contract and mint balance to a Wallet Contract-backed ETH-implicit account (`0x{sender}`), as in `test_erc20_emulation` [9](#0-8) .
2. Construct an Ethereum-style `Transaction2930` calling `ERC20_TRANSFER_SELECTOR` with `to` set to a 20-byte address that is a real Ethereum smart-contract-wallet address (no corresponding secp256k1 private key), and sign it with the sender's Wallet Contract secret key.
3. Submit via `rlp_execute`; `try_emulation` decodes `to` and derives `receiver_id = 0x{hex(to)}{suffix}` unconditionally, with no registrar lookup, and issues `ft_transfer` to that account [2](#0-1) .
4. The NEP-141 balance is now held by `0x{hex(to)}{suffix}` on NEAR. Since no one holds the secp256k1 private key matching that address (it was a smart-contract wallet on Ethereum), the account can never authorize a `rlp_execute` transfer of these tokens out, per the Wallet Contract's signature-verification requirement [10](#0-9) , permanently freezing the funds.

### Citations

**File:** docs/DataStructures/Account.md (L102-122)
```markdown
### ETH-implicit account ID

The account ID is derived from a Secp256K1 public key using the following formula: `'0x' + keccak256(public_key)[12:32].hex()`.

Example: a public key in base58 `2KFsZcvNUMBfmTp5DTMmguyeQyontXZ2CirPsb21GgPG3KMhwrkRuNiFCdMyRU3R4KbopMpSMXTFQfLoMkrg4HsT` will map to the account ID `0x87b435f1fcb4519306f9b755e274107cc78ac4e3`.

### Implicit account creation

An account with a NEAR-implicit or ETH-implicit account ID can only be created by sending a transaction/receipt with a single `Transfer` action to the implicit account ID receiver ([deterministic accounts](#deterministic-account-creation) have their own rules):

- The account will be created with the account ID.
- The account balance will have a transfer balance deposited to it.
- If this is NEAR-implicit account, it will have a new full access key with the ED25519-curve public key of `decode_hex(account_id)` and nonce `(block_height - 1) * MULTIPLIER` (to address an issues discussed [here](https://gov.near.org/t/issue-with-access-key-nonce/749)).
- If this is ETH-implicit account, it will have the [Wallet Contract](#wallet-contract) deployed, which can only be used by the owner of the Secp256K1 private key where `'0x' + keccak256(public_key)[12:32].hex()` matches the account ID.

Implicit account can not be created using `CreateAccount` action to avoid being able to hijack the account without having the corresponding private key.

Once a NEAR-implicit account is created it acts as a regular account until it's deleted.

An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
```

**File:** docs/DataStructures/Account.md (L128-129)
```markdown
Without going into details, an Ethereum-compatible wallet user sends a transaction to an RPC endpoint, which wraps it and passes it to the Wallet Contract (on the target account) as an `rlp_execute(target: AccountId, tx_bytes_b64: Vec<u8>)` contract call.
Then, the contract parses `tx_bytes_b64` and verifies it is signed with the private key matching the target [ETH-implicit account ID](#eth-implicit-account-id) on which the contract is hosted.
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L59-93)
```rust
        ERC20_TRANSFER_SELECTOR => {
            // We intentionally map to `u128` instead of `U256` because the NEP-141 standard
            // is to use u128.
            let (to, value): (Address, u128) =
                ethabi_utils::abi_decode(&ERC20_TRANSFER_SIGNATURE, &tx.data[4..])?;
            let receiver_id: AccountId = format!("0x{}{}", hex::encode(to), suffix)
                .parse()
                .unwrap_or_else(|_| env::panic_str("eth-implicit accounts are valid account ids"));

            // Include any data after the main args as a memo in the transfer.
            // The main data takes 68 bytes because there is a 4-byte selector followed
            // by two arguments which are each allocated 32 bytes according to the
            // Solidity ABI standard.
            let memo = if tx.data.len() > 68 {
                Some(format!(r#""0x{}""#, hex::encode(&tx.data[68..])))
            } else {
                None
            };
            let args = format!(
                r#"{{"receiver_id": "{}", "amount": "{}", "memo": {}}}"#,
                receiver_id.as_str(),
                value,
                memo.as_deref().unwrap_or("null"),
            );
            Ok((
                Action::FunctionCall {
                    receiver_id: target.to_string(),
                    method_name: "ft_transfer".into(),
                    args: args.into_bytes(),
                    gas: 2 * FIVE_TERA_GAS,
                    yocto_near: 1,
                },
                ParsableEthEmulationKind::ERC20Transfer { receiver_id, fee },
            ))
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-432)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L66-82)
```rust
    // The way an honest relayer assigns `target` is as follows:
    // 1. If the Ethereum transaction payload represents a Near action then use the receiver_id,
    // 2. If the payload looks like a supported Ethereum emulation then use the address registrar:
    // 2.a. if the tx.to address is registered then use the associated account id,
    // 2.b. otherwise, tx.to == target
    // 3. Otherwise, tx.to == target
    // Given this algorithm, the only way to have `TargetKind::EthImplicit` is in the
    // following cases:
    // I)   The Ethereum transaction payload is not parseable as a known action,
    // II)  The payload is parsable as a Near action and the receiver_id is an eth-implicit account
    // III) The payload is parsable as a supported Ethereum emulation but the to address is
    //      not registered in the address registrar.
    // Therefore, to determine if the relayer is honest we must always parse the payload and
    // we only need to check the registrar if the payload is parseable as an Ethereum emulation.
    // Note: the `TargetKind` is determined in `validate_tx_relayer_data` above, and that function
    // also confirms that the `target` is compatible with the user's `tx.to`.

```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L30-86)
```rust
    /// Computes the address associated with the given `account_id` and
    /// attempts to store the mapping `address -> account_id`. If there is
    /// a collision where the given `account_id` has the same address as a
    /// previously registered one then the mapping is NOT updated and `None`
    /// is returned. Otherwise, the mapping is stored and the address is
    /// returned as a hex-encoded string with `0x` prefix.
    #[payable]
    pub fn register(&mut self, account_id: AccountId) -> Option<String> {
        // It is not allowed to register eth-implicit accounts because the purpose
        // of the registry is to allow looking up the named account associated with
        // an address obtained via hashing, but eth-implicit accounts are already
        // parsable as addresses.
        if is_eth_implicit(&account_id) {
            let log_message = format!("Refuse to register eth-implicit account {account_id}");
            env::log_str(&log_message);
            return None;
        }

        // Must store the address and the account id
        let bytes_to_store = 20 + (account_id.len() as u128);
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }

        let address = account_id_to_address(&account_id);

        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
            Entry::Occupied(entry) => {
                let log_message = format!(
                    "Address collision between {} and {}. Keeping the former.",
                    entry.get(),
                    account_id
                );
                env::log_str(&log_message);
                // Transfer the deposit back to the caller since no storage was updated.
                let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
                env::promise_batch_action_transfer(refund_promise, given_deposit);
                None
            }
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs (L202-248)
```rust
// The Wallet Contract should understand the ERC-20 standard and map
// it to NEP-141 function calls.
#[tokio::test]
async fn test_erc20_emulation() -> anyhow::Result<()> {
    const MINT_AMOUNT: NearToken = NearToken::from_near(100);
    const TRANSFER_AMOUNT: NearToken = NearToken::from_near(32);
    const RELAYER_REFUND: NearToken = NearToken::from_millinear(3);
    const GAS_LIMIT: u64 = 100_000;

    let TestContext {
        worker,
        wallet_contract,
        wallet_sk,
        wallet_address,
        wallet_contract_bytes,
        ..
    } = TestContext::new().await?;

    let token_contract = nep141::Nep141::deploy(&worker).await?;
    token_contract.mint(wallet_contract.inner.id(), MINT_AMOUNT.as_yoctonear()).await?;

    // Check balance
    let transaction = aurora_engine_transactions::eip_2930::Transaction2930 {
        nonce: 0.into(),
        gas_price: 0.into(),
        gas_limit: 0.into(),
        to: Some(Address::new(account_id_to_address(
            &token_contract.contract.id().as_str().parse().unwrap(),
        ))),
        value: Wei::zero(),
        data: [
            crate::eth_emulation::ERC20_BALANCE_OF_SELECTOR.to_vec(),
            ethabi::encode(&[ethabi::Token::Address(wallet_address)]),
        ]
        .concat(),
        chain_id: CHAIN_ID,
        access_list: Vec::new(),
    };
    let signed_transaction = crypto::sign_transaction(transaction, &wallet_sk);

    let result = wallet_contract
        .rlp_execute(token_contract.contract.id().as_str(), &signed_transaction)
        .await?;

    let balance: U128 = serde_json::from_slice(result.success_value.as_ref().unwrap())?;
    assert_eq!(balance.0, token_contract.ft_balance_of(wallet_contract.inner.id()).await?);

```
