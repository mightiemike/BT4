This confirms the analog clearly. Any external, unprivileged sender on a source chain can deposit funds "to" an arbitrary Push Chain smart-contract recipient (`IsCEA=true`, e.g. `TxType_FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`), and the `UniversalPayload` — including `MaxFeePerGas` and `MaxPriorityFeePerGas` — is attacker-supplied data decoded from the source-chain event, never signed by or bound to the recipient. `UniversalPayload.ValidateBasic()` at [1](#0-0)  only checks the fields are valid non-negative uint256 strings; there is no upper bound on `MaxFeePerGas`. `DeductGasFeesFromReceipt` then computes `gasCost` using `CalculateGasCost(baseFee, MaxFeePerGas, MaxPriorityFeePerGas, gasUsed)` and burns it straight out of the **recipient's** own `upc` balance via `DeductAndBurnFees`, with the only bound being `gasUsed <= GasLimit` (also attacker-controlled) rather than any cap on price: [2](#0-1) . The execution path that reaches this for an arbitrary victim contract is `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload`'s smart-contract branch, driven purely by `utx.InboundTx.IsCEA` and `Recipient`, with no ownership check tying the payload to the recipient contract: [3](#0-2)  and [4](#0-3) .

### Title
Unbounded attacker-controlled `MaxFeePerGas`/`GasLimit` in CEA inbound payloads drains arbitrary recipient contracts' native balance - (File: `x/uexecutor/keeper/fees.go`, `x/uexecutor/types/universal_payload.go`)

### Summary
For `IsCEA=true` inbounds (`FUNDS_AND_PAYLOAD` / `GAS_AND_PAYLOAD`), the `UniversalPayload` — including `MaxFeePerGas`, `MaxPriorityFeePerGas`, and `GasLimit` — is fully attacker-controlled data decoded from a source-chain deposit event, not a value chosen or authorized by the recipient contract. When Universal Validators vote this inbound to quorum, `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` calls `CallExecuteUniversalTx` against the recipient and then `DeductGasFeesFromReceipt` burns `gasUsed * min(MaxFeePerGas, baseFee)`-derived cost from the **recipient's own** `upc` balance — a wallet the attacker neither owns nor funds. There is no upper bound anywhere on `MaxFeePerGas`/`MaxPriorityFeePerGas`.

### Finding Description
`UniversalPayload.ValidateBasic()` [1](#0-0)  only verifies the numeric fields parse as non-negative `uint256` strings. There is no ceiling check against a protocol/chain configured maximum fee or gas price. This payload arrives as part of `Inbound.UniversalPayload`, which for `IsCEA` inbounds is populated straight from source-chain calldata submitted by any external, unprivileged account — the `Recipient` field is simply whatever hex address the depositor chooses [5](#0-4) , with no requirement that the recipient consented to or even knows about the deposit.

After honest validators vote the inbound to quorum, the smart-contract branch calls `CallExecuteUniversalTx` against the victim contract and then unconditionally attempts `DeductGasFeesFromReceipt` [6](#0-5) . `DeductGasFeesFromReceipt` computes `gasCost` via `CalculateGasCost` (effective price = `min(baseFee, maxFeePerGas)` today just `baseFee`, times `gasUsed`) and burns it from the recipient's real `upc` balance through `DeductAndBurnFees` [2](#0-1) . The only sanity check is `gasUsed <= GasLimit`, and `GasLimit` itself is also attacker-supplied with no protocol-side maximum — so an attacker can, in principle, drive `GasLimit`/execution gas consumption up (e.g. via a heavy calldata payload) and push `MaxFeePerGas`/priority values arbitrarily high in payloads processed by future fee-market logic that actually uses `maxPriorityFeePerGas` (currently commented out, but the struct and ABI fully support it), so any recipient contract holding `upc` for gas is exposed to being billed an attacker-chosen amount for a call it never asked to receive.

### Impact Explanation
Any funded smart contract (CEA) on Push Chain that receives an unsolicited cross-chain deposit can have gas fees for an attacker-crafted call deducted and burned from its own native `upc` balance, with no cap on the fee rate the attacker can encode in the payload. This is a direct, unprivileged drain of victim-held funds triggered purely by an ordinary deposit transaction on the source chain — matching the "no limit on the amount of fee users have to pay" class of bug, except here the fee is deducted unilaterally by protocol code rather than requiring the victim to voluntarily pay it to reclaim funds.

### Likelihood Explanation
Reaching this path requires only: (1) a Push Chain-integrated source chain with inbound enabled, (2) a target contract funded with `upc` to pay gas, and (3) submitting a normal gateway deposit with `IsCEA=true`-style calldata targeting the victim as `Recipient`. No privileged role, validator collusion, or governance action is needed — honest UVs voting the inbound to quorum is sufficient to trigger the deduction.

### Recommendation
Enforce an upper bound on `MaxFeePerGas`/`MaxPriorityFeePerGas` and `GasLimit` in `UniversalPayload.ValidateBasic()` or in `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` before calling `DeductGasFeesFromReceipt`, ideally pinned to the chain's actual `baseFee`/fee-market parameters (e.g. reject payloads whose `MaxFeePerGas` exceeds some configurable multiple of current `baseFee`). Consider requiring recipient opt-in/allowlisting for CEA-initiated calls that can incur gas billing, so an unrelated depositor cannot force arbitrary contracts to pay for EVM execution they didn't request.

### Proof of Concept
1. Attacker picks a victim smart contract on Push Chain that holds `upc` (e.g. a DEX pool, vault, or any CEA integrator).
2. Attacker calls the configured gateway contract on any enabled source chain (e.g. `eip155:11155111`) to emit a deposit event with `IsCEA=true`, `Recipient = victimContract`, and a `UniversalPayload` with `GasLimit` and `MaxFeePerGas` set high, `Data` crafted to trigger meaningful execution in the victim (or simply a call whose `gasUsed` is nontrivial).
3. Honest Universal Validators observe and vote `MsgVoteInbound` to quorum, as demonstrated by the test harness patterns in [7](#0-6) .
4. `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` executes `CallExecuteUniversalTx` then `DeductGasFeesFromReceipt`, burning `gasCost` computed from the attacker-chosen `MaxFeePerGas`/`GasLimit` directly out of the victim contract's `upc` balance, with the victim having no say in the fee terms.

### Citations

**File:** x/uexecutor/types/universal_payload.go (L41-58)
```go
	// Validate all numeric string fields as uint256
	uintFields := map[string]string{
		"value":                    p.Value,
		"gas_limit":                p.GasLimit,
		"max_fee_per_gas":          p.MaxFeePerGas,
		"max_priority_fee_per_gas": p.MaxPriorityFeePerGas,
		"nonce":                    p.Nonce,
		"deadline":                 p.Deadline,
	}

	for fieldName, value := range uintFields {
		if value != "" {
			bi, ok := new(big.Int).SetString(value, 10)
			if !ok || bi.Sign() < 0 {
				return errors.Wrapf(sdkerrors.ErrInvalidRequest, "%s must be a valid unsigned integer", fieldName)
			}
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-102)
```go
	if utx.InboundTx.IsCEA {
		// isCEA path: recipient is explicitly specified.
		// Three-way check:
		//   1. Recipient is a UEA  → existing flow (deposit + ExecutePayloadV2)
		//   2. Recipient is a deployed smart contract (not UEA) → deposit + executeUniversalTx
		//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
		if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
			execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
		} else {
			ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
			} else if isUEA {
				// UEA path: deposit PRC20 into the UEA (if amount > 0), then execute payload via UEA
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
				// EOA: just deposit, skip executeUniversalTx (no contract to call)
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			}
		}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L216-256)
```go
	// Smart contract path (isCEA): call executeUniversalTx and return
	if isSmartContract {
		prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

		scAmount := new(big.Int)
		scAmount, ok := scAmount.SetString(utx.InboundTx.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
		}

		txId := common.HexToHash(utx.Id)

		var payload []byte
		if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
			payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
		}

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

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L315-352)
```go
	t.Run("gas fees deducted from smart contract recipient after executeUniversalTx", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals, contractAddr := setupInboundCEASmartContractTest(t, 4)

		// Fund the smart contract with upc so fee deduction can succeed
		contractAccAddr := sdk.AccAddress(contractAddr.Bytes())
		fundCoins := sdk.NewCoins(sdk.NewInt64Coin("upc", 1_000_000_000))
		require.NoError(t, chainApp.BankKeeper.MintCoins(ctx, "mint", fundCoins))
		require.NoError(t, chainApp.BankKeeper.SendCoinsFromModuleToAccount(ctx, "mint", contractAccAddr, fundCoins))

		balanceBefore := chainApp.BankKeeper.GetBalance(ctx, contractAccAddr, "upc")

		// Reach quorum
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}

		// Verify executeUniversalTx PCTx has gas_used > 0
		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.GreaterOrEqual(t, len(utx.PcTx), 2, "should have deposit + executeUniversalTx PCTxs")

		callPcTx := utx.PcTx[1]
		require.Equal(t, "SUCCESS", callPcTx.Status)
		require.Greater(t, callPcTx.GasUsed, uint64(0), "executeUniversalTx should report gas used")

		// Verify upc balance decreased (gas was deducted)
		balanceAfter := chainApp.BankKeeper.GetBalance(ctx, contractAccAddr, "upc")
		require.True(t, balanceAfter.Amount.LT(balanceBefore.Amount),
			"smart contract upc balance should decrease after gas fee deduction (before=%s, after=%s)",
			balanceBefore.Amount, balanceAfter.Amount)
	})
```
