### Title
Silent gas-fee corruption on unchecked `strconv.ParseUint(data.GasFee)` in Solana outbound signing — ([File: universalClient/chains/svm/tx_builder.go])

### Summary
Three Solana outbound tx-building paths (`GetOutboundSigningRequest`, `BuildOutboundTransaction`, `BuildRefRouteTransactions`) parse the outbound event's `GasFee` field with `strconv.ParseUint(data.GasFee, 10, 64)` and discard the error, while the sibling `amount` field is explicitly bounds-checked with `amount.IsUint64()` before use. This mirrors the reported `int224` truncation bug: an out-of-range numeric conversion is performed without validation, silently substituting a wrong value into security/accounting-critical data instead of failing.

### Finding Description
`GetOutboundSigningRequest` parses the outbound amount and explicitly guards it: [1](#0-0) 

But `GasFee` is parsed with the error thrown away: [2](#0-1) 

The same unchecked pattern is duplicated in `BuildOutboundTransaction`: [3](#0-2) 

and again in `BuildRefRouteTransactions` (confirmed via 3 total occurrences of `ParseUint(data.GasFee` in this file). Go's `strconv.ParseUint` does not clamp to 0 on overflow — per the standard library, if the value cannot be represented in the given bit size, it returns `ErrRange` and the *maximum representable value* (`math.MaxUint64`), which is silently accepted here because the error is ignored with `_`.

This corrupted `gasFee` value then flows directly into:
- The TSS signing message (`constructTSSMessage`), which is what all validators sign and what the on-chain SVM gateway program verifies: [4](#0-3) 
- The Borsh-encoded instruction data for `finalize_universal_tx`, `revert_universal_tx`, and `rescue_funds`: [5](#0-4) [6](#0-5) 

`data.GasFee` originates from the `UniversalTxOutbound` EVM event's `gasFee` field, a `uint256` unpacked as `*big.Int` and later serialized to a decimal string: [7](#0-6) 

That `gasFee` is computed on-chain by `GetOutboundTxGasAndFees` from the `UniversalCore` contract based on gas price/gas limit/protocol fee for the destination chain: [8](#0-7) 

Because the value is a `uint256` on the EVM side with no explicit cap enforced before this Go parsing step, any legitimate computation that produces a fee ≥ 2^64 (≈1.844e19, easily reachable for an 18-decimal gas/fee token at moderate nominal amounts, e.g. tens of tokens) will silently become `math.MaxUint64` in the signed message and instruction data, rather than the relayer failing loudly the way it does for `amount`.

### Impact Explanation
This breaks the "all validators must compute the same value — any drift here breaks the TSS signing hash" invariant that the code's own comments call out for the analogous fund-migration path. Here the invariant isn't broken across validators (all would independently and deterministically clamp to the same wrong `MaxUint64`), but it silently substitutes a wildly incorrect gas-fee figure into:
1. The keccak256 TSS-signed message that the on-chain gateway program uses to authorize the transaction,
2. The instruction data used for vault fee accounting on Solana.

If the on-chain program trusts this `gas_fee` field for vault fee-deduction/refund bookkeeping, a corrupted `MaxUint64` value could cause outbound finalize/revert/rescue transactions to fail unexpectedly (since no vault plausibly holds `2^64-1` lamports/tokens) — a denial of service on that specific outbound — or, if the receiving on-chain program does not strictly validate the field against actual vault balance, a mismatch between Push Chain's recorded `outbound.GasFee` accounting and what was actually signed/executed on Solana. This falls under the "corruption of gas fee accounting" and "TSS coordination" impact categories in the allowed scope.

### Likelihood Explanation
Likelihood is moderate-to-low: exploitability depends on whether gas-fee values in practice can exceed `2^64-1`. This is plausible for high-decimal PRC20/gas tokens or high-value outbounds under normal (non-malicious) usage, meaning it could be triggered inadvertently as well as by an attacker who intentionally selects a large-amount outbound routed through a high-fee gas token to push the computed `gasFee` past the `uint64` boundary. I was not able to fully verify from this repository alone what upper bound `GetOutboundTxGasAndFees` enforces on the EVM/Solidity side (that contract is not part of the indexed Go code), so I cannot confirm with certainty how easily an unprivileged user can drive this fee past `2^64-1` — this is the main source of uncertainty in likelihood.

### Recommendation
Do not discard the `strconv.ParseUint` error. Return an explicit error (as already done for `amount.IsUint64()`) when `data.GasFee` fails to parse or does not fit in a `uint64`, in all three call sites (`GetOutboundSigningRequest`, `BuildOutboundTransaction`, `BuildRefRouteTransactions`). This makes the failure mode identical to the amount-overflow guard rather than silently substituting `math.MaxUint64`.

### Proof of Concept
Conceptual PoC (cannot be executed without the full SVM gateway on-chain program, which is out of this repo):
1. Cause (via ordinary usage or intentionally) an outbound event where the emitted `gasFee` (uint256) exceeds `18446744073709551615` (2^64-1) — e.g., a token with 18 decimals and a nominal fee ≥ ~18.45 tokens.
2. `universalClient` fetches this `OutboundCreatedEvent.GasFee` string and calls `GetOutboundSigningRequest`, which executes:
   `gasFee, _ = strconv.ParseUint(data.GasFee, 10, 64)`
   Since the value is out of range, `gasFee` silently becomes `18446744073709551615` instead of erroring.
3. This corrupted `gasFee` is baked into the keccak256 TSS-signed message (`constructTSSMessage`) and the Borsh instruction payload (`buildWithdrawAndExecuteData` / `buildRevertData` / `buildRescueData`).
4. All honest validators reproduce the same (silently wrong) value deterministically and sign it, so the corrupted fee is never caught by consensus — it is only caught (if at all) when the Solana gateway program attempts to act on a nonsensical `gas_fee` of `2^64-1`, most likely causing the finalize/revert/rescue instruction to fail against real vault balances.

### Citations

**File:** universalClient/chains/svm/tx_builder.go (L205-214)
```go
	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", data.Amount)
	}

	// Validate amount fits in u64 (Solana uses u64 for amounts, events use uint256)
	if !amount.IsUint64() {
		return nil, fmt.Errorf("amount exceeds u64 max: %s", data.Amount)
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L292-296)
```go
	// Parse gas fee from event data
	var gasFee uint64
	if data.GasFee != "" {
		gasFee, _ = strconv.ParseUint(data.GasFee, 10, 64)
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L764-768)
```go
	// Parse gas fee from event data
	var gasFee uint64
	if data.GasFee != "" {
		gasFee, _ = strconv.ParseUint(data.GasFee, 10, 64)
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L1423-1428)
```go
	amountBytes := make([]byte, 8)
	binary.BigEndian.PutUint64(amountBytes, amount)
	message = append(message, amountBytes...)

	gasFeeBytes := make([]byte, 8)
	binary.BigEndian.PutUint64(gasFeeBytes, gasFee)
```

**File:** universalClient/chains/svm/tx_builder.go (L1751-1753)
```go
	gasFeeBytes := make([]byte, 8)
	binary.LittleEndian.PutUint64(gasFeeBytes, gasFee)
	data = append(data, gasFeeBytes...)
```

**File:** universalClient/chains/svm/tx_builder.go (L1810-1812)
```go
	gasFeeBytes := make([]byte, 8)
	binary.LittleEndian.PutUint64(gasFeeBytes, gasFee)
	data = append(data, gasFeeBytes...)
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L84-85)
```go
	event.GasFee = values[i].(*big.Int)
	i++
```

**File:** x/uexecutor/keeper/gas_fee.go (L26-63)
```go
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
```
