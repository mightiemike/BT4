Confirmed: `ExecuteInboundGasAndPayload`'s `isSmartContract` branch is structurally identical to `ExecuteInboundFundsAndPayload`'s, and it exhibits the exact same third-party-contract gas-drain issue.

### Title
Attacker-Controlled Inbound `Recipient` Drains Arbitrary Deployed Contract's Native Balance via `DeductGasFeesFromReceipt` in `ExecuteInboundGasAndPayload` - (File: `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
For a `GAS_AND_PAYLOAD` inbound with `IsCEA=true`, when `utx.InboundTx.Recipient` is a non-UEA address that already has deployed bytecode, `isSmartContract` is set true and the module calls `CallExecuteUniversalTx` against that arbitrary contract, then immediately calls `DeductGasFeesFromReceipt` to bill that contract's own native UPC balance for the gas the module-originated call consumed — all driven by attacker-controlled inbound fields (`Recipient`, `UniversalPayload.Data`, `MaxFeePerGas`, `MaxPriorityFeePerGas`, `GasLimit`).

### Finding Description
In `ExecuteInboundGasAndPayload` [1](#0-0) , when the recipient is not a UEA but has code, `isSmartContract` is set and later the smart-contract branch executes: [2](#0-1) 

This is functionally identical to the `isSmartContract` branch in `ExecuteInboundFundsAndPayload`: [3](#0-2) 

Both call `k.CallExecuteUniversalTx(cacheCtx, ueaAddr, ...)` with `ueaAddr` set directly from the attacker-supplied `utx.InboundTx.Recipient` [4](#0-3) , and on success invoke `k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)`. Inside `DeductGasFeesFromReceipt`, the fee is computed purely from attacker-controlled `MaxFeePerGas`/`MaxPriorityFeePerGas`/`GasLimit` in the inbound payload and the actual `receipt.GasUsed`, and is deducted from `recipient` (the arbitrary contract) via `DeductAndBurnFees`, which transfers-and-burns coins straight from that address's account: [5](#0-4) .

Both code paths wrap the EVM call and fee deduction in a `CacheContext`, only committing (`writeCache()`) if fee deduction succeeds [6](#0-5) . This closes the "free execution" gap (arbitrary code execution without paying) but does **not** address the underlying issue: the target of `CallExecuteUniversalTx` and the payer of gas is any deployed contract the attacker names as `Recipient`, not a party that consented to be billed. An unprivileged attacker sends an ordinary cross-chain deposit (any nonzero amount, or with `IsCEA=true`) naming an arbitrary already-deployed Push Chain contract (e.g., a DEX pool, a DApp treasury, or any contract holding native UPC for its own future gas needs) as `Recipient`. If `CallExecuteUniversalTx` succeeds against that contract (e.g., it has a fallback/receive function or matches an arbitrary selector without reverting), the module silently drains real UPC from that unrelated contract's native balance to cover gas billed at attacker-chosen rates, up to `GasLimit`.

### Impact Explanation
This is an unauthorized fund drain: an unprivileged, unrelated contract's native balance is spent as gas payment for a call it never authorized, driven entirely by attacker-supplied inbound message fields. This falls squarely within the "stealing/draining ... user or protocol-controlled funds" and "unauthorized module-originated EVM execution" impact categories, and is reachable purely through ordinary user deposit/inbound submission — no privileged actor is required.

### Likelihood Explanation
High. Any user can submit a standard cross-chain deposit with `IsCEA=true`, `Recipient` set to any deployed contract address on Push Chain that is not a UEA, and craft `UniversalPayload` fields (`Data`, `MaxFeePerGas`, `MaxPriorityFeePerGas`, `GasLimit`) to trigger and bill the call. The only precondition is that `CallExecuteUniversalTx` against the target does not revert (many contracts with fallback functions or that don't validate `msg.sender`/selector will not revert).

### Recommendation
Do not bill gas fees to an arbitrary third-party recipient contract that never opted into this billing model. Either restrict the `isSmartContract`/`executeUniversalTx` path to contracts that explicitly register/opt in for module-originated calls and gas billing, or fund gas from a source tied to the inbound sender/attacker (e.g., pre-funded escrow from the deposit itself) rather than debiting the target contract's own balance. At minimum, gate this path so it cannot target contracts that hold unrelated user/protocol funds.

### Proof of Concept
1. Attacker identifies a deployed Push Chain contract `C` (not a UEA) holding native UPC (e.g., for its own operational gas) and that has a fallback/receive function or otherwise won't revert on arbitrary calldata.
2. Attacker submits (or has relayed) a standard inbound deposit event from a supported source chain with `IsCEA=true`, `Recipient=C`, an arbitrary `UniversalPayload.Data`, and attacker-chosen `MaxFeePerGas`/`MaxPriorityFeePerGas`/`GasLimit`.
3. `ExecuteInboundGasAndPayload` detects `C` has code, sets `isSmartContract=true`, and calls `CallExecuteUniversalTx` against `C` [7](#0-6) .
4. If the call to `C` does not revert, `DeductGasFeesFromReceipt` burns UPC from `C`'s own balance based on attacker-chosen gas parameters [8](#0-7) , draining funds `C`'s owner never authorized to be spent this way.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L67-70)
```go
				if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
					execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
				} else {
					ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)
```

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L233-256)
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

		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
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
