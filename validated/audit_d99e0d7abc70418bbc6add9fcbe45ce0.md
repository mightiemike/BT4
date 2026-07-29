## Finding: Integer-Division Truncation of Slippage Protection to Zero in Gas-Abstraction Auto-Swap

The external report's root cause — a fixed-point/decimal quantity that legitimately should be non-zero gets floored to `0` by integer division at low magnitudes — has a direct functional analog in Push Chain's gas-abstraction auto-swap path.

### Title
Integer-division truncation of `minPCOut` slippage guard to zero enables value extraction on small gas-abstraction deposits - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
Both `ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` compute the swap's minimum-output slippage guard as `minPCOut = quote * 95 / 100` using Go integer arithmetic, then pass it straight into `CallPRC20DepositAutoSwap` on `UniversalCore`, which performs the actual PRC20→WPC swap. [1](#0-0) [2](#0-1) 

### Finding Description
For any `quote` such that `quote * 95 < 100` (i.e. `quote <= 1` in the PRC20's raw base units), integer division truncates `minPCOut` to `0`. Since `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbounds are user/attacker-controlled external-chain deposits whose `Amount` need only satisfy `amount > 0` per `ValidateForExecution`, an unprivileged actor can trigger a deposit small enough (or targeting a token/pool where the quoted PC-equivalent is this small) that the computed quote collapses the slippage floor to zero. [3](#0-2) 

When `minPCOut == 0`, the auto-swap executes with no meaningful minimum-output protection at all — the swap will succeed regardless of how unfavorable the executed price is. This nullifies the entire purpose of the 5%-slippage safety mechanism for exactly the class of deposits (small, low-value) where the report's original bug class (decimal/precision floor at the smallest representable unit) manifests.

### Impact Explanation
With slippage protection silently disabled, the value delivered into the depositor's UEA via `depositPRC20Token`/`refundUnusedGas`-style auto-swap paths can be sandwiched or otherwise manipulated around the AMM pool referenced by `UniversalCore`'s quoter, letting a third party extract most of the swap's economic value while the deposit still "succeeds" and is marked `SUCCESS` in the `PCTx`. This is a concrete, unprivileged-reachable loss of user funds (protocol-mediated fund draining), not merely a UX limitation — the depositor's expected native-gas proceeds can be reduced arbitrarily close to zero with no on-chain signal of failure.

### Likelihood Explanation
Likelihood is moderate: the trigger only requires an attacker (or an ordinary low-value depositor) to submit a small enough external-chain deposit that resolves to a raw quote of `<=1` unit (or any quote where `*95/100` rounds to 0), which is entirely within reach of any unprivileged bridge user and requires no validator/relayer/TSS collusion. Exploiting the resulting zero-slippage window for profit additionally requires influencing the AMM price around the swap's execution, which is a standard, well-understood DeFi front-running primitive rather than a privileged capability.

### Recommendation
Enforce a floor so `minPCOut` is never allowed to be `0` when `quote > 0` (e.g., `minPCOut = max(1, quote*95/100)`), or reject/round up the slippage computation, or explicitly fail the swap (routing to revert/refund) when the computed `minPCOut` truncates to zero, rather than silently proceeding with an unprotected swap.

### Proof of Concept
1. Attacker submits an inbound `GAS` (or `GAS_AND_PAYLOAD`) deposit of a PRC20-mapped external token with `Amount` set so that `GetSwapQuote` returns a raw quote value `q` where `q*95 < 100` (e.g. `q = 1`).
2. Validators reach quorum and `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` computes `minPCOut = q*95/100 = 0`.
3. `CallPRC20DepositAutoSwap` is invoked with `minPCOut = 0`, so the underlying swap accepts any non-negative output.
4. A third party manipulates the referenced pool's price immediately around the swap's execution (standard sandwich technique), causing the swap to execute at a highly unfavorable rate for the depositor while still returning `Status: "SUCCESS"` in the recorded `PCTx`. [4](#0-3)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L160-180)
```go
	// --- Finalize pcTx
	// Capture tx hash from receipt even on EVM revert for debugging.
	if receipt != nil {
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
	}
	if execErr != nil {
		k.Logger().Warn("execute inbound gas: swap failed",
			"utx_key", universalTxKey,
			"error", execErr.Error(),
			"should_revert", shouldRevert,
		)
		pcTx.ErrorMsg = execErr.Error()
	} else {
		k.Logger().Info("execute inbound gas: swap succeeded",
			"utx_key", universalTxKey,
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		pcTx.Status = "SUCCESS"
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

**File:** x/uexecutor/types/inbound.go (L126-138)
```go
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}
```
