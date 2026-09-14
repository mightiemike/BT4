### Title
External caller's attached deposit is permanently lost when `rlp_execute` fails before a promise is created - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Wallet Contract's `rlp_execute` entry point tracks an external caller's attached deposit via `CallerDeposit` so that it can be refunded if the resulting cross-contract call fails. However, this refund mechanism only fires from the `rlp_execute_callback` (i.e., after a `Promise` has actually been dispatched). Any error that causes `inner_rlp_execute` to return `Err(_)` *before* a promise is created — most notably `Error::User(_)` from RLP/ABI parsing failures — drops the already-computed `CallerDeposit` on the floor and returns `PromiseOrValue::Value(...)` with no refund promise at all, permanently stranding the caller's attached NEAR in the wallet contract's balance.

### Finding Description
`CallerDeposit::new` is computed once at the very start of `inner_rlp_execute`, based on `attached_deposit` and whether the caller is external (`predecessor_account_id != current_account_id`): [1](#0-0) 

Immediately after, `inner_rlp_execute` attempts to parse the RLP transaction. If parsing produces a `User` error, the function increments the nonce and returns `Err(err)` directly — discarding the `caller_deposit` value it just computed: [2](#0-1) 

Back in `rlp_execute`, this `Err(e)` case is converted straight into an `ExecuteResponse` value (not a `Promise`), with no code path that ever creates a refund transfer to the caller: [3](#0-2) 

Refunding only happens inside `rlp_execute_callback`, which is exclusively reachable through the `Promise` branch (i.e., only when `inner_rlp_execute` succeeds in building a promise chain, or via the relayer-ban promise path): [4](#0-3) 

This mirrors the ThunderNFT bug class: a value-bearing operation (`#[payable] rlp_execute`, analogous to `place_order`/`execute_order`) that accepts a caller's attached asset (deposit ≈ NFT) but has a validation/error branch which fails to correctly account for that already-received asset, permanently trapping it instead of returning/using it. The wallet contract's own test suite (`test_caller_refunds`) demonstrates the *intended* invariant — that external callers should be refunded on failure — but only exercises the "cross-contract call fails after promise dispatch" case, not the earlier "parsing fails before a promise is ever created" case, leaving this path unverified and vulnerable.

### Impact Explanation
Any unprivileged external account (not the wallet owner/relayer) that calls `rlp_execute` on someone else's eth-implicit wallet contract with a nonzero attached deposit and a malformed/invalid RLP-encoded transaction (triggering a `UserError`, e.g. bad ABI selector, invalid public key encoding, malformed access key account id, etc.) will have that attached deposit permanently absorbed into the wallet contract's account balance with no recovery path. This is a concrete, transaction-triggered, permanent loss of user funds — reachable by a single unprivileged RPC/transaction call — matching the "permanently frozen/lost funds" impact category.

### Likelihood Explanation
High reachability: any account can call `FunctionCall` with `rlp_execute` and an attached deposit against any eth-implicit account's wallet contract, with attacker-fully-controlled `tx_bytes_b64` content designed to trigger a `UserError` during parsing (before nonce/promise logic completes). No special privileges, races, or validator collusion are required — a single crafted transaction suffices, and the loss is deterministic given a `User` parsing error co-occurring with a nonzero attached deposit from an external predecessor.

### Recommendation
In `inner_rlp_execute`, whenever a `User` (or any other) error occurs after `CallerDeposit::new` has produced `Some(_)`, the caller's deposit must be explicitly refunded before returning the error — e.g., by returning the `CallerDeposit` alongside the `Error` so that `rlp_execute` can synchronously issue `Promise::new(account_id).transfer(...)` (or equivalent) in all early-return error branches, not just inside `rlp_execute_callback`. Alternatively, restructure so that no attached deposit is consumed/held until after parsing succeeds, e.g., by validating the transaction bytes prior to accepting/keeping the deposit, or immediately creating a best-effort refund promise in every error branch of `rlp_execute`.

### Proof of Concept
1. Deploy the Wallet Contract as a global contract for an eth-implicit account `W` (as in `test_wallet_contract_interaction`). [5](#0-4) 
2. From an unrelated external account `Caller` (predecessor != `W`), call `W.rlp_execute(target, tx_bytes_b64)` attaching a nonzero deposit, where `tx_bytes_b64` is crafted to decode to a Near-native action that triggers a `UserError` during `internal::parse_rlp_tx_to_action` (e.g., an `AddKey` action with an invalid public key encoding, hitting `construct_public_key`'s `Error::User(UserError::InvalidEd25519Key)` path). [6](#0-5) 
3. Observe that `rlp_execute` returns `PromiseOrValue::Value(ExecuteResponse { success: false, .. })` and no refund receipt/promise is ever created for `Caller`. [7](#0-6) 
4. Compare `Caller`'s balance before and after: it has decreased by the attached deposit amount (minus gas), and `W`'s balance has correspondingly increased — the deposit is now permanently part of `W`'s balance, unlike the `test_caller_refunds` scenario which only covers post-promise failures. [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L302-316)
```rust
fn construct_public_key(public_key_kind: u8, public_key: &[u8]) -> Result<PublicKey, Error> {
    if public_key_kind > 1 {
        return Err(Error::User(UserError::UnknownPublicKeyKind));
    }
    let mut bytes = Vec::with_capacity(public_key.len() + 1);
    bytes.push(public_key_kind);
    bytes.extend_from_slice(public_key);
    bytes.try_into().map_err(|_| {
        if public_key_kind == 0 {
            Error::User(UserError::InvalidEd25519Key)
        } else {
            Error::User(UserError::InvalidSecp256k1Key)
        }
    })
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L106-127)
```rust
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }

                ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Failed Near promise".into()),
                }
            }
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L389-393)
```rust
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L245-256)
```rust
    // Deploy the wallet contract as a global contract for ETH implicit accounts.
    let magic_bytes = wallet_contract_magic_bytes(chain_id);
    let wallet_code = wallet_contract(*magic_bytes.hash()).unwrap();
    let deploy_tx = SignedTransaction::deploy_global_contract(
        1,
        relayer.clone(),
        wallet_code.code().to_vec(),
        &relayer_signer.signer,
        *genesis_block.hash(),
        GlobalContractDeployMode::CodeHash,
    );
    height = check_tx_processing(&mut env, deploy_tx, height, blocks_number);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L197-213)
```rust
    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );
```
