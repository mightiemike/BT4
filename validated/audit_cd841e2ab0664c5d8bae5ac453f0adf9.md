Based on my investigation, I found a plausible analog: the NEAR wallet contract (the ETH-implicit account's global contract, invoked as an unprivileged path when any user submits a NEAR/relayed Ethereum-style transaction targeting an NEP-141 token) hard-codes both the token storage-deposit amount and the gas budgets used for its cross-contract calls into token contracts, instead of deriving them dynamically per-target-contract.

### Title
Hard-coded NEP-141 storage-deposit amount and fixed callback gas budgets in the NEAR wallet contract can permanently block ETH-implicit-account token transfers - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract` (the global contract used by ETH-implicit accounts, reachable by any unprivileged relayer/signer via `rlp_execute`) hard-codes `NEP_141_STORAGE_DEPOSIT_AMOUNT = 1_250 * MICRO_NEAR` and a set of small fixed `Gas` constants (`NEP_141_STORAGE_DEPOSIT_GAS`, `NEP_141_STORAGE_BALANCE_OF_GAS`, `RLP_EXECUTE_CALLBACK_GAS`, etc.) that are used whenever the wallet contract emulates an ERC20-style/NEP-141 transfer on behalf of an ETH-implicit account. [1](#0-0) 

### Finding Description
The comment at the constant definition explicitly documents the same class of assumption as the reported Connext bug (a fixed value substituted for a value that should be looked up/derived dynamically per counterparty): "This storage deposit value is the one used by the standard NEP-141 implementation, which essentially all tokens use. Therefore we hard-code it here instead of doing the extra on-chain call to `storage_balance_bounds`." [2](#0-1) 

Any token contract whose actual `storage_balance_bounds().min` exceeds the hard-coded `1_250 * MICRO_NEAR`, or whose `ft_transfer`/`storage_deposit` execution genuinely requires more compute than the fixed `NEP_141_STORAGE_DEPOSIT_GAS`/`NEP_141_STORAGE_BALANCE_OF_GAS` (5 Tgas each) budgets, will cause the cross-contract call chain issued by the wallet contract to revert (insufficient deposit or `GasExceeded`) on every attempt, exactly as the zero-slippage `xcall` in the report always reverts for any pool with real slippage. Because these constants are compiled into the deployed global contract code rather than being parameters that can be tuned per request, a user whose ETH-implicit account needs to interact with such a token has no way to work around the fixed values from the calling transaction.

### Impact Explanation
If reachable, this would render token operations initiated via `rlp_execute` against affected NEP-141 contracts permanently unusable for the ETH-implicit account — the promise chain will always fail, and any deposit routed through the flow is refunded (or burned, per the deposit-refund/gas-refund semantics in `runtime/runtime/src/lib.rs`) rather than reaching the destination, functionally freezing the intended value movement for that class of contract interactions.

### Likelihood Explanation
This is **not confirmed** to be currently exploitable to any severity meeting the bar: the constant is explicitly documented as matching "essentially all" existing NEP-141 tokens' storage bounds, and the code path is confined to `runtime/near-wallet-contract`, whose actual deposit/gas-forwarding logic and error handling (e.g., how `RelayerError::InsufficientGas` and deposit-related callbacks are structured beyond the constants shown) I was not able to fully trace within the available iterations. I could not verify from the retrieved snippets whether a shortfall in the hard-coded amount causes a hard revert versus a caught/handled error that degrades gracefully (e.g., returning `ExecuteResponse{success:false}` rather than losing funds), nor whether any refund path fully returns the deposit to the original owner. Given the uncertainty about the exact failure/refund mechanics and that the report's precondition (a token whose bounds/gas needs exceed the hard-coded constants) is described in the code itself as an edge case rather than the common path, I cannot assert with confidence that this reaches the "concrete unauthorized value movement / permanently frozen funds" bar required by the validation rules.

### Recommendation
If pursuing this further, a background agent should:
1. Read the full flow in `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs` and `eth_emulation.rs` to trace exactly how `NEP_141_STORAGE_DEPOSIT_AMOUNT`/`*_GAS` constants are used in the `ft_transfer`/`storage_deposit` promise chain, and what happens to the attached deposit/gas on failure (refund vs burn vs stuck).
2. Determine whether any real, currently-deployed NEP-141 contract has `storage_balance_bounds().min > 1_250 * MICRO_NEAR`, or requires more than 5 Tgas for `storage_deposit`/`storage_balance_of`, which would make the failure mode concretely reachable rather than theoretical.
3. If a genuine fund-freezing path is confirmed, consider making the deposit/gas parameters either query-derived (with a safety cap to prevent the griefing scenario the comment already guards against) or configurable, and ensure the failure path fully refunds the sender rather than silently dropping value.

### Proof of Concept
Not established. I could not construct or verify an end-to-end reproduction within the available tool budget — this would require deploying a synthetic NEP-141 token with `storage_balance_bounds().min` set above `1_250 * MICRO_NEAR` (or one whose `storage_deposit`/`storage_balance_of` genuinely burns more than 5 Tgas), then driving an ETH-implicit account through `rlp_execute` to interact with it and observing whether the deposit is refunded or lost. This should be done in a Devin session with full file/test access (`runtime/near-wallet-contract/implementation/wallet-contract/src/tests/`) to confirm before treating this as a validated finding.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-41)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
/// This storage deposit value is the one used by the standard NEP-141 implementation,
/// which essentially all tokens use. Therefore we hard-code it here instead of doing
/// the extra on-chain call to `storage_balance_bounds`. This also prevents malicious
/// token contracts with very high `storage_balance_bounds` from taking lots of $NEAR
/// from eth-wallet-contract users.
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```
