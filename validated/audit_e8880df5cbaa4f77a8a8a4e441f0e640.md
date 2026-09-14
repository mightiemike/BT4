### Title
Front-runnable relayer fee theft in `WalletContract::rlp_execute` via unauthenticated caller-based refund routing - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` accepts an RLP-encoded Ethereum transaction (`tx_bytes_b64`) that is fully signed and self-contained by the wallet owner, but the method itself performs no check that the Near account calling it (`env::predecessor_account_id()`) is the relayer the owner intended to use. Because the relayer's fee refund is paid to whichever account is the caller's `predecessor_account_id`, any third party who observes a pending `rlp_execute` call in the mempool can resubmit the identical `target`/`tx_bytes_b64` payload from their own account with higher gas price, consume the wallet's nonce first, and redirect the relayer fee to themselves — exactly the "front-run a state-gated call to hijack/deny the intended actor" pattern described in the reference report (there, PartyA front-runs to flip quote status and block PartyB's privileged action; here, an attacker front-runs to flip the nonce/consume the action and hijack the value meant for the honest relayer).

### Finding Description
`rlp_execute` is a public, non-private method guarded only by an in-flight-transaction flag, not by caller identity: [1](#0-0) 

The inner logic validates only that the *signed Ethereum transaction* (`tx_bytes_b64`) is well-formed and addressed to this wallet with the expected nonce — it never checks who the Near `predecessor_account_id` calling `rlp_execute` is: [2](#0-1) 

When the parsed transaction is an emulated base-token or ERC-20 transfer with a non-zero `fee`, the fee is refunded to `context.predecessor_account_id` — i.e., whichever account happened to be the caller of `rlp_execute` — as long as that account differs from the wallet itself: [3](#0-2) 

Because `tx_bytes_b64` is a complete, replay-safe-looking (but not caller-bound) blob that must appear as plaintext calldata in the honest relayer's transaction before it lands on-chain, any account can copy it verbatim into their own `rlp_execute(target, tx_bytes_b64)` call and front-run the honest relayer (e.g., with a higher gas price/priority). The wallet contract has no signature or attestation binding the fee-refund recipient to a specific registered relayer for this un-keyed call path (this is exactly the "external relayer" flow exercised by `test_base_token_transfer_with_relayer_refund` and the ERC-20 relayer-refund test, which use an arbitrary Near account, not a dedicated access key, as the relayer): [4](#0-3) [5](#0-4) 

The `nonce` is incremented as soon as the attacker's copy is processed: [6](#0-5) 
so the honest relayer's original transaction, once included, fails `validate_tx_relayer_data`'s nonce check (`Error::Relayer(RelayerError::InvalidNonce)`), wasting the honest relayer's gas and denying them the fee they were promised by the user — the mirror image of PartyA front-running `emergencyClosePosition` to flip state and block PartyB's expected, fee/position-bearing action.

### Impact Explanation
This directly causes unauthorized value movement: a relayer-designated fee (paid by the wallet owner in yoctoNEAR, computed from `max_fee_per_gas * gas_limit`) is redirected to an attacker who did nothing but copy public calldata and front-run, while the intended relayer's transaction is invalidated and its gas wasted. This qualifies as concrete unauthorized value movement/fee-bypass reachable purely from a public RPC/mempool observation and an ordinary transaction submission — no privileged access required.

### Likelihood Explanation
Likelihood is high wherever this contract is used with fee-bearing base-token or ERC-20 emulated transfers and an "external" (non-access-key) relayer model, since:
- `tx_bytes_b64` must be visible as plaintext transaction calldata before inclusion (standard mempool visibility).
- Front-running via higher gas price is a well-known, cheap technique on NEAR.
- No additional secret or authorization is required to replay the exact same call from a different account.

### Recommendation
Bind the relayer fee refund to an authorized/attested relayer identity instead of the raw `predecessor_account_id` of the call, e.g., have the owner's signed RLP transaction (or a companion NEP-366-style signed wrapper) explicitly name the authorized relayer account and refuse to pay the fee to any other caller; alternatively, require relayers to act through a registered `FunctionCall` access key (as in the "internal relayer" flow) for any fee-bearing transaction, and disable/deprioritize third-party fee refunds for the unauthenticated "external relayer" call path.

### Proof of Concept
1. Wallet owner signs an Ethereum EOA base-token transfer (or ERC-20 transfer) with `nonce = N` and non-zero `fee`, and hands the resulting `tx_bytes_b64` off-chain to relayer R for submission via `rlp_execute(target, tx_bytes_b64)` — see the `RELAYER_REFUND` mechanics exercised in [7](#0-6) .
2. Attacker A observes R's pending transaction (which necessarily contains `tx_bytes_b64` as plaintext function-call args) in the mempool.
3. A submits its own transaction calling `rlp_execute(target, tx_bytes_b64)` on the same wallet contract, with itself as signer/predecessor and a higher gas price to be included first.
4. The wallet contract processes A's call: `inner_rlp_execute` validates the embedded Ethereum tx (only checks it targets this wallet and matches expected nonce `N`), increments `nonce` to `N+1`, and schedules the fee refund promise to `context.predecessor_account_id` = A (per [8](#0-7) ).
5. R's transaction, once included, now fails `validate_tx_relayer_data`'s nonce check (`nonce != expected_nonce`) because the wallet's nonce is already `N+1`, so R receives nothing and wastes the gas they spent constructing/submitting the transaction, while A has stolen the fee.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-114)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L348-365)
```rust
    let (action, transaction_kind) = match parsing_result {
        Ok((action, transaction_kind)) => {
            // Increment nonce for all cases where the registrar contract is not needed
            // to prevent replay of those transactions. For transactions that go through
            // the registrar we still do not know if the transaction has a relayer error
            // or not, therefore we must delay incrementing the nonce.
            //
            // Note: relayers with access keys cannot use this delay to needlessly spend
            // the users tokens because only one transaction is allowed to be in-flight
            // at a time.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L367-385)
```rust
            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
                }
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L318-368)
```rust
fn validate_tx_relayer_data<'a>(
    tx: &NormalizedEthTransaction,
    target: &'a AccountId,
    context: &ExecutionContext,
    expected_nonce: u64,
) -> Result<TargetKind<'a>, Error> {
    if tx.address.raw() != context.current_address {
        return Err(Error::Relayer(RelayerError::InvalidSender));
    }

    if tx.chain_id != Some(CHAIN_ID) {
        return Err(Error::Relayer(RelayerError::InvalidChainId));
    }

    let to = tx.to.ok_or(Error::User(UserError::EvmDeployDisallowed))?.raw();

    let target_kind = parse_target(target, context.current_address);

    // valid targets satisfy `to == target` or `to == hash(target)`
    let is_valid_target = match target_kind {
        TargetKind::CurrentAccount if to == context.current_address => {
            target == &context.current_account_id
        }
        TargetKind::EthImplicit(address) if to == address => {
            target.as_str()
                == format!("0x{}{}", hex::encode(address), context.current_account_suffix())
        }
        _ => to == account_id_to_address(target),
    };

    if !is_valid_target {
        return Err(Error::Relayer(RelayerError::InvalidTarget));
    }

    let nonce = if tx.nonce <= U64_MAX {
        tx.nonce.low_u64()
    } else {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    };
    if nonce != expected_nonce {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    }

    // Relayers must attach at least as much gas as the user requested.
    let gas_limit = if tx.gas_limit < U64_MAX { tx.gas_limit.as_u64() } else { u64::MAX };
    if env::prepaid_gas().as_gas() < gas_limit.saturating_mul(GAS_MULTIPLIER) {
        return Err(Error::Relayer(RelayerError::InsufficientGas));
    }

    Ok(target_kind)
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs (L132-200)
```rust
// Relayers are paid for base token transfers.
#[tokio::test]
async fn test_base_token_transfer_with_relayer_refund() -> anyhow::Result<()> {
    const TRANSFER_AMOUNT: NearToken = NearToken::from_near(2);
    const RELAYER_REFUND: NearToken = NearToken::from_millinear(1);
    const GAS_LIMIT: u64 = 100_000;

    let TestContext { worker, wallet_contract, wallet_sk, wallet_contract_bytes, .. } =
        TestContext::new().await?;

    let relayer = worker.root_account()?;

    let (other_wallet, other_address) =
        TestContext::deploy_wallet(&worker, &wallet_contract_bytes).await?;

    let initial_relayer_balance = relayer.view_account().await?.balance;
    let initial_wallet_balance = wallet_contract.inner.as_account().view_account().await?.balance;
    let initial_other_balance = other_wallet.inner.as_account().view_account().await?.balance;

    let transaction = aurora_engine_transactions::eip_2930::Transaction2930 {
        nonce: 0.into(),
        gas_price: (RELAYER_REFUND.as_yoctonear()
            / ((GAS_LIMIT as u128) * (MAX_YOCTO_NEAR as u128)))
            .into(),
        gas_limit: GAS_LIMIT.into(),
        to: Some(Address::new(other_address)),
        value: Wei::new_u128(TRANSFER_AMOUNT.as_yoctonear() / u128::from(MAX_YOCTO_NEAR)),
        data: Vec::new(),
        chain_id: CHAIN_ID,
        access_list: Vec::new(),
    };
    let signed_transaction = crypto::sign_transaction(transaction, &wallet_sk);

    let result = wallet_contract
        .rlp_execute_from(
            &relayer,
            other_wallet.inner.id().as_str(),
            &signed_transaction,
            NearToken::from_yoctonear(0),
        )
        .await?;

    assert!(result.success);

    let final_relayer_balance = relayer.view_account().await?.balance;
    let final_wallet_balance = wallet_contract.inner.as_account().view_account().await?.balance;
    let final_other_balance = other_wallet.inner.as_account().view_account().await?.balance;

    // Receiver balance increases
    assert_eq!(
        final_other_balance.as_yoctonear(),
        initial_other_balance.as_yoctonear() + TRANSFER_AMOUNT.as_yoctonear()
    );

    // Wallet balance decreases (round to milliNEAR to account for funds
    // received for calling the contract).
    assert_eq!(
        final_wallet_balance.as_millinear(),
        initial_wallet_balance.as_millinear()
            - TRANSFER_AMOUNT.as_millinear()
            - RELAYER_REFUND.as_millinear()
    );

    // Relayer balance stays the same (rounded to the nearest milliNEAR) since the
    // wallet refunded the relayer approximately equal to the transaction gas cost.
    assert_eq!(final_relayer_balance.as_millinear(), initial_relayer_balance.as_millinear());

    Ok(())
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs (L367-428)
```rust

    // If an external relayer triggers the transaction then it is
    // compensated for the Near gas.
    let transaction = aurora_engine_transactions::eip_2930::Transaction2930 {
        nonce: 4.into(),
        gas_price: (RELAYER_REFUND.as_yoctonear()
            / ((GAS_LIMIT as u128) * (MAX_YOCTO_NEAR as u128)))
            .into(),
        gas_limit: GAS_LIMIT.into(),
        to: Some(Address::new(account_id_to_address(
            &token_contract.contract.id().as_str().parse().unwrap(),
        ))),
        value: Wei::zero(),
        data: [
            crate::eth_emulation::ERC20_TRANSFER_SELECTOR.to_vec(),
            ethabi::encode(&[
                ethabi::Token::Address(other_address),
                ethabi::Token::Uint(TRANSFER_AMOUNT.as_yoctonear().into()),
            ]),
        ]
        .concat(),
        chain_id: CHAIN_ID,
        access_list: Vec::new(),
    };
    let signed_transaction = crypto::sign_transaction(transaction, &wallet_sk);

    let relayer = worker.root_account()?;
    let initial_relayer_balance = relayer.view_account().await?.balance;
    let initial_wallet_balance = wallet_contract.inner.as_account().view_account().await?.balance;

    let result = wallet_contract
        .rlp_execute_from(
            &relayer,
            token_contract.contract.id().as_str(),
            &signed_transaction,
            NearToken::from_yoctonear(0),
        )
        .await?;

    assert!(result.success);
    assert_eq!(
        MINT_AMOUNT.as_yoctonear() - (3 * TRANSFER_AMOUNT.as_yoctonear()),
        token_contract.ft_balance_of(wallet_contract.inner.id()).await?
    );
    assert_eq!(
        3 * TRANSFER_AMOUNT.as_yoctonear(),
        token_contract.ft_balance_of(other_wallet.inner.id()).await?
    );

    let final_relayer_balance = relayer.view_account().await?.balance;
    let final_wallet_balance = wallet_contract.inner.as_account().view_account().await?.balance;

    // Relayer balance stays the same (rounded to the nearest milliNEAR) since the
    // wallet refunded the relayer approximately equal to the transaction gas cost.
    assert_eq!(final_relayer_balance.as_millinear(), initial_relayer_balance.as_millinear());

    // Wallet balance decreases (round to milliNEAR to account for funds
    // received for calling the contract).
    assert_eq!(
        final_wallet_balance.as_millinear(),
        initial_wallet_balance.as_millinear() - RELAYER_REFUND.as_millinear()
    );
```
