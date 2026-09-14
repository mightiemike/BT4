### Title
NEAR Wallet Contract permanently retains an external caller's attached deposit when `rlp_execute` fails before spawning a promise - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` is a `#[payable]` entry point that an unprivileged NEAR account can call directly (as `predecessor_account_id`) with an attached NEAR deposit, analogous to a caller sending `msg.value` to a contract call. When the inner parsing/validation of the RLP-encoded Ethereum transaction fails with certain error variants (`Error::User`, `Error::AccountId`, or a `Error::Relayer` triggered by a non-owner caller), `inner_rlp_execute` returns `Err` before any `Promise` (and therefore before any refund promise) is created, and `rlp_execute` simply converts that error into a `PromiseOrValue::Value(...)` response without ever refunding the caller's attached deposit.

### Finding Description
`rlp_execute` is marked `#[payable]`, so any attached deposit is credited to the contract's account balance the moment the method executes [1](#0-0) . The refund path for that deposit is entirely manual: it is threaded through a `CallerDeposit` struct that is only ever consumed in the "happy path" where a `Promise` is spawned, and specifically refunded from `rlp_execute_callback` when `PromiseResult::Failed` is observed [2](#0-1) .

`inner_rlp_execute` computes `caller_deposit` and would only pass it forward if parsing succeeds (`Ok((action, transaction_kind))`) [3](#0-2) . However, if `parse_rlp_tx_to_action` returns an error, the function returns `Err(err)` directly without ever using `caller_deposit` [4](#0-3) .

Back in the top-level `rlp_execute` handler, this `Err` is handled as:
```rust
Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => { ... ban_relayer ... }
Err(e) => PromiseOrValue::Value(e.into()),
``` [5](#0-4) 

Neither branch refunds `env::attached_deposit()`. The `ban_relayer` promise (spawned via `create_ban_relayer_promise`) only revokes the relayer's access key; it contains no transfer action. For any external caller that is not using an access key from the wallet's owner (i.e., an arbitrary NEAR account calling `rlp_execute` on someone else's eth-implicit wallet contract, or hitting a `UserError`/`AccountIdError` path), the attached deposit is silently absorbed into the wallet contract's balance permanently, with no code path that ever schedules a transfer back to the caller.

The error taxonomy documents this as an acceptable design tradeoff only for the *gas fee* the relayer spends chasing its own mistake ("they are spending their own resources on the gas... we do not care") [6](#0-5)  — but this reasoning does not extend to the NEAR *token deposit* the caller attached to the call, which is a separate, larger value than gas and is not mentioned at all in that justification.

### Impact Explanation
Any account (owner, relayer, or arbitrary third party) that calls `rlp_execute` with a non-zero attached deposit and triggers a parse-time error (malformed RLP/base64, wrong signer, bad chain id, unsupported action, `ExcessYoctoNear`, invalid public key encoding, etc.) loses that deposit permanently — it becomes stuck in the wallet contract's balance with no method exposed to reclaim it (the contract only ever sends fee/refund transfers from within the successful-parse code paths). This is a genuine, unauthorized, permanent loss of user funds triggered by a single unprivileged transaction/contract call, matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
High reachability: this requires only one ordinary `FunctionCall` transaction with a deposit attached to `rlp_execute`, from any account, with a slightly malformed or adversarially-triggerable `tx_bytes_b64` argument (e.g., invalid base64, or a transaction whose ecrecover-derived sender does not match the wallet's address — trivially producible by anyone who does not hold the wallet's private key). No validator collusion, no privileged role, and no race condition is needed.

### Recommendation
In `inner_rlp_execute`/`rlp_execute`, ensure that the attached deposit is refunded to `predecessor_account_id` on every error path that does not otherwise consume it in a promise chain — i.e., have the top-level `Err(e) => ...` branch (and the relayer-ban branch) construct a promise that includes a transfer of `env::attached_deposit()` back to the caller whenever `predecessor_account_id != current_account_id`, mirroring the refund-on-failure logic already implemented in `rlp_execute_callback`.

### Proof of Concept
1. Deploy/observe an eth-implicit account with the `WalletContract` code (per NEP for eth-implicit accounts).
2. From any NEAR account `attacker.near` (not the wallet owner), call `rlp_execute(target, tx_bytes_b64)` attaching a non-trivial NEAR deposit, where `tx_bytes_b64` is a syntactically-invalid base64 string (or a validly-encoded RLP transaction signed by a key other than the wallet's owner key, producing `Error::Relayer(RelayerError::InvalidSender)` since `attacker.near` is not the owner's registered access key holder and thus is not banned).
3. `ExecutionContext::new` succeeds (address extraction only depends on `current_account_id`), `CallerDeposit::new` captures the deposit, but `parse_rlp_tx_to_action` fails and returns `Err(...)` before `caller_deposit` is ever forwarded [7](#0-6) .
4. `rlp_execute` returns `PromiseOrValue::Value(e.into())` with no compensating transfer [8](#0-7) .
5. Query `attacker.near`'s balance before/after: it is reduced by the deposit amount (plus gas), and the wallet contract's balance is increased by the deposit amount with no mechanism to return it — the funds are permanently stuck.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-128)
```rust
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
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-312)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-409)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);

    let parsing_result = internal::parse_rlp_tx_to_action(&tx_bytes_b64, &target, &context, *nonce);
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

            (action, transaction_kind)
        }
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
        Err(err) => {
            // Do not increment nonce on Relayer or AccountId errors.
            // The latter error is an issue in the deployment (so the nonce is meaningless).
            // The former arises from the relayer itself doing something wrong and thus the
            // user's transaction could still be valid and potentially submitted properly by
            // another relayer. To allow this we do not increment the nonce.
            //
            // Note: if a relayer is using an access key for this wallet then that key will
            // still be revoked (in the main logic of `rlp_execute`). This fact together with
            // the condition that there only be one in-flight transaction at a time implies
            // that a relayer cannot maliciously burn a large portion of the user's tokens.
            // If the relayer is not using an access key then they are spending their own
            // resources on the gas and therefore we do not care if the relayer submits
            // the same faulty transaction multiple times.
            return Err(err);
        }
```
