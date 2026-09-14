Based on the investigation, I've found a strong structural analog in the `near-wallet-contract` (the NEAR Wallet Contract implementing Ethereum-tx emulation for eth-implicit accounts).

### Title
Wallet Contract lets the account owner set the relayer refund fee to zero, bypassing compensation for relayed transactions - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs` / `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
In the `WalletContract`, an Ethereum-style transaction signed by the wallet owner encodes a `tx_fee` (derived from `max_fee_per_gas * gas_limit`) that is meant to compensate the relayer for base-token/ERC-20 "emulation" transfers. This fee is entirely chosen by the transaction signer (the wallet owner) and is never validated against the relayer's actual gas cost; the on-chain refund is only sent `if !fee.is_zero()`. A malicious or careless wallet owner can therefore sign a transaction with `max_fee_per_gas = 0` (or negligibly small), which produces `tx_fee = 0`, causing the wallet contract to skip the relayer-refund promise entirely while still executing the requested transfer/call.

### Finding Description
`internal::parse_rlp_tx_to_action` computes `tx_fee` purely from user-controlled fields of the signed Ethereum transaction: [1](#0-0) 

This fee is then used in `inner_rlp_execute` to conditionally create a refund promise to the relayer (`predecessor_account_id`), but only `if !fee.is_zero()`: [2](#0-1) 

There is no on-chain minimum-fee enforcement, no comparison of `fee` against `env::prepaid_gas()` or the relayer's actual gas price, and no protocol-level requirement that a base-token/ERC-20 emulation carry any compensation at all. The only check performed is `InsufficientGas` in `validate_tx_relayer_data`, which validates that enough gas was *attached*, not that the relayer will be *paid* for that gas: [3](#0-2) 

The code comments explicitly acknowledge this trust gap ("Users should always verify the fee before signing... Relayers should also verify the fee before sending"), mirroring the fusion-swap report's pattern where fee correctness is left to off-chain/UI enforcement rather than on-chain validation.

### Impact Explanation
This maps to the report's "fee bypass" bug class: an unprivileged, self-interested actor (the wallet owner, who is simply an unprivileged EOA/implicit-account holder submitting a signed message) can omit or zero out a fee intended to compensate a third party (the relayer) for real, on-chain resource expenditure (attached NEAR gas). Because `rlp_execute` is `#[payable]` and reachable by any relayer forwarding any user's signed bytes via a standard `FunctionCall` transaction, this is directly reachable from an ordinary submitted transaction — no validator, peer, or operator privilege is required. The effect is a value-transfer/fee bypass at the smart-contract layer: the relayer pays real NEAR for gas and receives nothing back, while the wallet owner's requested transfer/call still completes.

However, per the same reasoning that caused the original report to be rated as low-severity ("Acknowledged" without protocol changes), the loss here is confined to a relayer that *voluntarily* chose to serve the transaction and can independently detect and refuse to relay any signed transaction whose `fee` is insufficient before submitting it (this is explicitly the mitigation called out in the code's own comments). There is no unauthorized movement of a third party's or the protocol's funds, no supply inflation, and no state-root divergence — an economically rational relayer simply declines to service free-riding requests, exactly analogous to the resolvers/takers in the original report refusing to fill misconfigured orders.

### Likelihood Explanation
Low-to-moderate. Any wallet owner can trivially construct a signed Ethereum-style transaction with `max_fee_per_gas = 0`. The only actors harmed are relayers that fail to pre-validate the fee before spending their own gas to submit the transaction — a check the contract's own documentation instructs relayers to perform. Well-behaved relayer implementations are expected to simulate/verify the fee off-chain before forwarding, which significantly limits realistic exploitation to naive or automated relayer software.

### Recommendation
Enforce a minimum on-chain relationship between `fee` and the actual cost the relayer incurs (e.g., require `fee >= env::prepaid_gas() * some_minimum_price`, or make the refund mandatory/non-zero for non-self predecessor calls), rather than allowing the wallet owner to unilaterally set `fee = 0`. Alternatively, since this is an application-layer trust assumption already explicitly documented in-code as relayer responsibility, no protocol-level fix is strictly required beyond continuing to document the risk clearly for relayer implementers — consistent with how the original fusion-swap finding was ultimately "Acknowledged" rather than remediated on-chain.

### Proof of Concept
1. A wallet owner controls an eth-implicit NEAR account backed by the `WalletContract`.
2. They construct and sign an Ethereum-formatted transaction (e.g., an `EOABaseTokenTransfer` or `ERC20Transfer`) with `max_fee_per_gas = 0`, requesting a transfer of value to some `target`.
3. They send the signed bytes to a relayer, who calls `rlp_execute(target, tx_bytes_b64)` on the `WalletContract`, paying for the gas of this call themselves.
4. Inside `inner_rlp_execute`, `tx_fee` computed from `max_fee_per_gas.saturating_mul(gas_limit) = 0`, so the `if !fee.is_zero()` branch is skipped and no refund promise to the relayer is created (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:381-385`).
5. The requested transfer/call still executes successfully; the wallet owner receives the intended value movement while the relayer receives zero compensation for the gas it spent submitting the transaction. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L54-64)
```rust
    // Compute the fee based on the user's Ethereum transaction.
    // This is sent as a refund to the relayer in the case of an emulated base token
    // transfer or ERC-20 transfer. The reason for this refund is that it allows a
    // user with $NEAR to use a relayer service from their wallet immediately without
    // additional on-boarding.
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L361-365)
```rust
    // Relayers must attach at least as much gas as the user requested.
    let gas_limit = if tx.gas_limit < U64_MAX { tx.gas_limit.as_u64() } else { u64::MAX };
    if env::prepaid_gas().as_gas() < gas_limit.saturating_mul(GAS_MULTIPLIER) {
        return Err(Error::Relayer(RelayerError::InsufficientGas));
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
