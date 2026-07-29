### Title
Hardcoded 5% swap-slippage in `depositPRC20WithAutoSwap` permanently freezes CEA-routed inbound funds with no revert path - (File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go)

### Summary
The external report describes an Archimedes bug where a hardcoded slippage buffer used to size a swap can be too small relative to actual pool conditions, causing the swap-dependent close-out to revert (`Not enough LvUSD in pool`) even though the user holds enough funds to close the position — and critically, there is no fallback, leaving the position permanently stuck. Push Chain's `uexecutor` module has a structurally identical pattern in its gas/token auto-swap path for `isCEA`-routed inbound funds: a hardcoded `95%` (`minPCOut = quote * 95 / 100`) slippage guard is used for `depositPRC20WithAutoSwap`, and if the DEX quote-to-execution price moves beyond that hardcoded 5% band (normal, attacker-uninvolved market conditions, MEV, or multi-hop AMM slippage), the swap call reverts — and, unlike the non-CEA path, the CEA path is explicitly coded to **never create an `INBOUND_REVERT` outbound**, so the inbound funds are neither delivered to the recipient nor refunded to the source chain.

### Finding Description
In `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, the `isCEA` branch (lines 61-99) calls `k.gasAndPayloadDepositAutoSwap` (lines 347-378), which computes a swap quote via `GetSwapQuote` and applies a hardcoded slippage floor: [1](#0-0) 

If `CallPRC20DepositAutoSwap` reverts on-chain because the actual swap output falls below `minPCOut` (analogous to the Archimedes `Not enough LvUSD in pool` revert), `execErr` is set. The code explicitly documents that this failure path is terminal for CEA inbounds: [2](#0-1) [3](#0-2) 

Unlike the non-CEA branch (line 146-154), where `shouldRevert = true` is set on deposit failure and an `INBOUND_REVERT` outbound is built via `buildRevertOutbound`/`attachOutboundsToUtx` to return funds to the source chain, the CEA branch never sets `shouldRevert`. When `execErr != nil && utx.InboundTx.IsCEA`, the function simply records a `FAILED` `PCTx` and returns `nil` — no revert outbound is ever created: [4](#0-3) 

The identical pattern (hardcoded slippage + swap call that can revert on ordinary price movement) also appears in the non-payload gas path, `ExecuteInboundGas` (`execute_inbound_gas.go:142-153`), and in `applyGasRefund` (`x/uexecutor/keeper/outbound.go:213-237`), though the latter has an explicit no-swap fallback that avoids permanent loss. The same "isCEA failures never revert" design choice is also documented/repeated in `x/uexecutor/keeper/execute_inbound_funds_and_payload.go:53-103` and `x/uexecutor/keeper/execute_inbound_funds.go:74-86` (though those paths don't involve the swap/slippage calculation itself — they cover other CEA-failure causes with the same no-revert design).

This inbound flow is driven by honestly-observed and voted-on on-chain events from the source chain (i.e., it is reached whenever quorum of Universal Validators votes an inbound with `IsCEA=true`, `TxType_GAS_AND_PAYLOAD`), meaning any ordinary user submitting a legitimate cross-chain deposit whose swap execution happens to slip beyond the hardcoded 5% band (due to normal AMM price movement between quote time and execution time, low liquidity, or MEV sandwiching on the internal Uniswap-style pool) will have their bridged funds permanently stuck: the tokens are never delivered to the intended recipient (UEA/smart contract), and no revert/refund is ever issued back to the source chain.

### Impact Explanation
This matches the "permanent freezing of user funds" impact class from the allowed-impact gate. A legitimate, unprivileged user's cross-chain deposit becomes unrecoverable purely due to an internal accounting/slippage-guard design flaw — no malicious relayer, validator, or admin action is required. The funds are neither delivered nor refunded, and there is no documented recovery path (the admin `RevertStuckInbound` mechanism operates on stuck *ballots*, not on UTXs that already finalized with a FAILED `PcTx` and no outbound).

### Likelihood Explanation
Likelihood is significant given normal operating conditions: a Uniswap-V3-style quote (`GetSwapQuote`/`quoteExactInputSingle`) is computed at vote/finalization time but executed one or more blocks later in `CallPRC20DepositAutoSwap`; any pool price movement, low liquidity, or competing transactions exceeding 5% between quote and execution — entirely plausible for smaller internal liquidity pools or during volatile periods — triggers the revert. This requires no attacker action at all; it is a byproduct of ordinary usage, exactly like the original Archimedes report's finding that ordinary slippage/imbalance can silently exceed a hardcoded buffer.

### Recommendation
For the `isCEA` gas/payload deposit-autoswap path, treat swap-revert failures the same as other pre-deposit failures: set `shouldRevert = true` and build an `INBOUND_REVERT` outbound (or, alternatively, fall back to a no-swap direct PRC20 deposit to the recipient, mirroring the fallback already implemented in `applyGasRefund`). Additionally, reconsider the fixed 5% slippage constant — make it configurable/dynamic, and/or re-fetch the quote immediately before executing the swap in the same atomic call to minimize the quote-to-execution drift window.

### Proof of Concept
1. A user submits (via a Universal Validator-observed source-chain deposit) an inbound with `TxType_GAS_AND_PAYLOAD`, `IsCEA=true`, targeting a valid UEA recipient, depositing an external gas token that requires auto-swap to PC's wrapped native token.
2. Quorum of honest Universal Validators votes and finalizes the inbound; `ExecuteInboundGasAndPayload` is invoked.
3. `gasAndPayloadDepositAutoSwap` fetches `quote` via `GetSwapQuote` and computes `minPCOut = quote * 95 / 100`.
4. Before/while `CallPRC20DepositAutoSwap` executes on-chain (`depositPRC20WithAutoSwap`), normal price movement on the internal DEX pool (e.g., low liquidity, a few blocks of drift, or another swap in the same block) pushes the realized output below `minPCOut`, causing the underlying contract call to revert; `execErr != nil`.
5. Since `utx.InboundTx.IsCEA == true`, execution reaches lines 211-214: a `FAILED` `PcTx` is recorded and the function returns `nil` — no `INBOUND_REVERT` outbound is built.
6. The user's bridged funds are never delivered to the recipient UEA and never returned to the source chain; they are permanently stuck in the Universal Executor module's control with no on-chain path to recovery.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L61-66)
```go
			if utx.InboundTx.IsCEA {
				// isCEA path: recipient is explicitly specified.
				// Three-way check:
				//   1. Recipient is a UEA  → deposit + autoswap + ExecutePayloadV2
				//   2. Recipient is a deployed smart contract (not UEA) → deposit + autoswap + executeUniversalTx
				//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L190-214)
```go
	// --- create revert ONLY for pre-deposit / deposit failures (non-isCEA path)
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}

		return nil
	}

	// isCEA failures: record FAILED PCTx but no revert
	if execErr != nil && utx.InboundTx.IsCEA {
		return nil
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```
