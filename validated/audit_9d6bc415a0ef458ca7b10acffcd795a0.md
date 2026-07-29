## Analysis

Confirmed: `ExecuteInboundGasAndPayload` contains an `isSmartContract` branch that is structurally identical to the one in `ExecuteInboundFundsAndPayload`, and it exhibits the same third-party-contract gas-drain behavior.

In `ExecuteInboundGasAndPayload`, when `utx.InboundTx.IsCEA` is true and `Recipient` resolves to a non-UEA address with deployed bytecode, `isSmartContract` is set to `true` [1](#0-0) . The module then deposits/auto-swaps funds into that arbitrary `ueaAddr` and unconditionally calls `CallExecuteUniversalTx` on it inside a `CacheContext`, invoking the `executeUniversalTx(...)` selector with attacker-controlled `sourceChain`, `ceaAddress`, and `payload` arguments against whatever contract the attacker names as `Recipient` [2](#0-1) . If that call succeeds (e.g., the target happens to implement a matching selector, has a permissive fallback, or the attacker crafts calldata that collides with any external function), `DeductGasFeesFromReceipt` is invoked with `ueaAddr` — the attacker-named third-party contract — as the fee payer [3](#0-2) .

`DeductGasFeesFromReceipt` computes gas cost purely from the EVM receipt's `GasUsed` and the caller-supplied `UniversalPayload.MaxFeePerGas`/`MaxPriorityFeePerGas`, then calls `DeductAndBurnFees`, which does `SendCoinsFromAccountToModule` + `BurnCoins` against the `recipient` account with no ownership, allowance, or authorization check [4](#0-3) . Because `recipient` here is `ueaAddr` (the attacker-chosen `Recipient` field, coerced via `sdk.AccAddress(recipient.Bytes())`), this burns real native `upc` balance from whatever contract account happens to occupy that address — not an account that consented to pay gas for this EVM call [5](#0-4) .

This matches exactly the pattern already identified in `ExecuteInboundFundsAndPayload`'s `isSmartContract` branch, which performs the identical sequence: `CallExecuteUniversalTx` on `ueaAddr` then `DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, ...)` [6](#0-5) . Both entry paths (GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD isCEA inbounds) share the same root-cause: `CallExecuteUniversalTx` is dispatched to an arbitrary, attacker-named contract address without verifying that this contract is a UEA/opted into being billed this way, and the resulting gas is billed to that contract's own native balance via `DeductGasFeesFromReceipt`.

The `CacheContext` wrapping only ensures atomicity between the EVM call and fee deduction (both commit or both roll back together) — it does not add an authorization check on whose balance is drained [7](#0-6) .

### Title
Unauthorized native-balance drain of arbitrary third-party contracts via isCEA `GAS_AND_PAYLOAD` inbound `Recipient` targeting — ([File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
The `isSmartContract` branch of `ExecuteInboundGasAndPayload` mirrors the same flaw already present in `ExecuteInboundFundsAndPayload`: an unprivileged user submitting a GAS_AND_PAYLOAD isCEA inbound can set `Recipient` to any deployed, non-UEA contract address on Push Chain. The module invokes `executeUniversalTx` on that address and, if the call succeeds for any reason, bills the resulting gas cost against that arbitrary contract's own native `upc` balance via `DeductGasFeesFromReceipt`/`DeductAndBurnFees`, with no check that the target contract is a UEA that consented to this billing model.

### Impact Explanation
Any deployed contract that holds native `upc` balance (e.g., a DEX pool, vault, or another user's wallet-like contract) can have its funds burned by an attacker who names it as `Recipient` in an inbound message and crafts calldata/selector collisions that let `executeUniversalTx` succeed against it. This is an unauthorized burn of third-party protocol/user funds, matching the "unauthorized burn"/"draining" impact category.

### Likelihood Explanation
Requires only an ordinary unprivileged inbound deposit/message with `IsCEA=true` and an attacker-chosen `Recipient`; no privileged actor is needed. The success of the drain depends on the `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` call succeeding against an arbitrary target contract that doesn't implement this interface — likely only functions via selector/fallback collisions or contracts that happen to accept this calldata shape — but the code path itself contains no authorization gate preventing the attempt or the subsequent balance deduction.

### Recommendation
Before charging gas fees to `ueaAddr` in the `isSmartContract` branch, verify the target explicitly opted in to this billing (e.g., only bill known/registered UEA-compatible contracts, or require the contract to implement and pass an interface/ERC-165-style check), and avoid deducting gas fees from an address that never authorized the module to spend on its behalf. Apply the same fix consistently to both `execute_inbound_funds_and_payload.go` and `execute_inbound_gas_and_payload.go`.

### Proof of Concept
1. Attacker submits an inbound event with `IsCEA=true`, `Recipient` = address of a deployed contract `V` known to hold native `upc` balance and not registered as a UEA.
2. `ExecuteInboundGasAndPayload` resolves `isUEA=false` for `V`, sees it has code, sets `isSmartContract=true` [8](#0-7) .
3. Deposit/auto-swap proceeds into `V`'s PRC20/native balance.
4. `CallExecuteUniversalTx` invokes `executeUniversalTx(...)` on `V` with attacker-controlled `payload` [9](#0-8) ; if this succeeds (selector collision/fallback), a receipt with non-zero `GasUsed` is returned.
5. `DeductGasFeesFromReceipt(cacheCtx, cacheCtx, V, receipt, universalPayload)` burns `gasCost` worth of `upc` from `V`'s own account [5](#0-4) , funds `V` never authorized to spend.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L84-98)
```go
					} else {
						// Non-UEA: check if recipient has code (smart contract) vs EOA
						codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
						if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
							isSmartContract = true
						}
						// EOA: just deposit, skip executeUniversalTx
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L233-248)
```go
		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L250-256)
```go
		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```

**File:** x/uexecutor/keeper/fees.go (L97-140)
```go
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L239-255)
```go
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
```

**File:** x/uexecutor/keeper/evm.go (L646-692)
```go
// CallExecuteUniversalTx calls executeUniversalTx on a smart-contract recipient.
// This is used for isCEA inbounds whose recipient is a deployed contract (not a UEA).
func (k Keeper) CallExecuteUniversalTx(
	ctx sdk.Context,
	recipientAddr common.Address,
	sourceChain string,
	ceaAddress []byte,
	payload []byte,
	amount *big.Int,
	prc20AssetAddr common.Address,
	txId [32]byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	recipientABI, err := types.ParseRecipientContractABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse recipient contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		recipientABI,
		ueModuleAccAddress,
		recipientAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"executeUniversalTx",
		sourceChain,
		ceaAddress,
		payload,
		amount,
		prc20AssetAddr,
		txId,
	)
}
```
