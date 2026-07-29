Based on my investigation, I've confirmed the key finding: for the isCEA (Custom Executor Address) inbound path, `DeductGasFeesFromReceipt` charges gas fees to an arbitrary third-party recipient contract's own `upc` balance, using `MaxFeePerGas`/`MaxPriorityFeePerGas`/`GasLimit`/`Data` fields that are fully attacker-controlled as part of the source-chain `UniversalPayload`, with `recipient` chosen arbitrarily by the attacker (any deployed contract address, whether or not it consents to being called).

### Title
Attacker-Controlled `isCEA` Inbound Payload Drains Arbitrary Recipient Contract's Native `upc` Gas Balance - (File: `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/fees.go`)

### Summary
This is the Push Chain analog of the CommunityPool `refundGasByUser()` issue: the original bug let a price-setting party (the node, via `tx.gasprice`) unilaterally decide the amount extracted from a victim's balance (the message-originating user or SChain owner) with no bound tied to the victim's consent. In Push Chain's `isCEA` inbound execution flow, an unprivileged attacker who merely emits an event on any supported source chain (e.g. Sepolia) can name **any deployed smart contract on Push Chain** as the `Recipient`/`UniversalPayload.To`, supply arbitrary `Data` (executed via `executeUniversalTx`), and set an arbitrary `GasLimit`/`MaxFeePerGas`/`MaxPriorityFeePerGas`. Push Chain validators objectively observe and vote this crosschain event to quorum (no privileged approval needed), the module then calls `executeUniversalTx` on the named contract as `ueModuleAccAddress`, and afterwards deducts the resulting gas cost **from the named recipient contract's own native `upc` balance** — a contract that never authorized this call, never opted into CEA routing, and has no relationship with the attacker.

### Finding Description
The isCEA branch of inbound execution resolves the `Recipient` field of an attacker-supplied `Inbound`/`UniversalPayload` directly, without any allowlist or opt-in check that the target contract intends to receive/pay for `executeUniversalTx` calls: [1](#0-0) 

After executing the attacker's calldata via `CallExecuteUniversalTx`, the module invokes `DeductGasFeesFromReceipt`, passing `ueaAddr` (the attacker-named recipient) as the payer: [2](#0-1) 

`DeductGasFeesFromReceipt` computes the amount to burn from the recipient's real `upc` balance using `MaxFeePerGas`/`MaxPriorityFeePerGas`/`GasLimit` taken from the attacker-controlled `UniversalPayload`, then unconditionally deducts and burns that amount from the recipient's account: [3](#0-2) 

`UniversalPayload.ValidateBasic()` only checks that these numeric fields are valid non-negative uint256 strings — it does not check that the recipient consented, nor cap `GasLimit`/`MaxFeePerGas` to any sane bound: [4](#0-3) 

There is no CEA registration/allowlist mechanism found anywhere in the module (`x/uexecutor`/`x/uregistry`) gating which contract addresses may be targeted by `isCEA=true` inbounds — `ValidateForExecution` only checks that `Recipient` is a syntactically valid hex address: [5](#0-4) 

Effectively, the attacker (relayed honestly by honest Universal Validators) chooses: (1) the victim (any contract holding `upc`), (2) the calldata executed against it (bounded only by the real EVM opcodes it triggers), and (3) the nominal `GasLimit` up to which real gas may be consumed and later billed to the victim — while the attacker pays nothing on Push Chain (the module account, not the attacker, is billed as `msg.sender` for `DerivedEVMCall`, and the source-chain event itself can be a cheap, low-value transaction).

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... unauthorized burn ... of user or protocol-controlled funds" in the allowed impact gate. An attacker can repeatedly target any contract that has integrated `executeUniversalTx` and holds `upc` for its own operations, forcing it to pay gas for arbitrary attacker-chosen calldata it never requested, draining its native balance over time — a direct crosschain analog of the original report's "extort funds from users/owner via unbounded, attacker-influenced gas billing."

### Likelihood Explanation
Likelihood is high for any deployed contract that implements `executeUniversalTx` (a documented, expected integration surface — see `x/uexecutor/types/abi.go` `RecipientContractABI`) and holds a native `upc` balance for its own gas needs. No validator collusion, admin action, or special privilege is required — only an ordinary source-chain transaction from the attacker and honest quorum voting by Universal Validators on the objectively-true observation.

### Recommendation
Require CEA recipient contracts to explicitly opt in (e.g., via a registry entry or an on-chain signal read from the contract itself, such as a dedicated view function or allowlist maintained by governance) before `executeUniversalTx`/gas billing can target them, and/or make the attacker's own bridged value (rather than the recipient's unrelated balance) the funding source for gas billed against arbitrary calldata. Alternately, cap per-inbound gas billing to a small, protocol-fixed limit unless the target has pre-funded/pre-approved a gas budget for CEA calls.

### Proof of Concept
1. Attacker deploys nothing; picks any existing contract `V` on Push Chain that implements `executeUniversalTx` and holds `upc` (e.g., a DEX router, vault, or other integrated contract).
2. Attacker emits a source-chain (e.g., Sepolia) event with `Recipient = V`, `IsCEA = true`, `TxType = GAS_AND_PAYLOAD`, `UniversalPayload.To = V`, arbitrary `Data`, `GasLimit = 21000000`, high `MaxFeePerGas`.
3. Universal Validators honestly observe and vote this inbound to quorum; `ExecuteInboundGasAndPayload` executes `CallExecuteUniversalTx` against `V` with the attacker's calldata.
4. `DeductGasFeesFromReceipt` burns `baseFee * gasUsed` from `V`'s own `upc` balance — see the existing test confirming this behavior for legitimate use, which equally proves the drain path for an unrelated/unconsenting `V`: [6](#0-5) 
5. Repeat with different crafted `Data` (e.g., gas-heavy loops within `GasLimit`) to maximize `gasUsed` per inbound, draining `V`'s balance across many cheap source-chain transactions.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L61-89)
```go
			if utx.InboundTx.IsCEA {
				// isCEA path: recipient is explicitly specified.
				// Three-way check:
				//   1. Recipient is a UEA  → deposit + autoswap + ExecutePayloadV2
				//   2. Recipient is a deployed smart contract (not UEA) → deposit + autoswap + executeUniversalTx
				//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
				if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
					execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
				} else {
					ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

					_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
					if ueaCheckErr != nil {
						execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
					} else if isUEA {
						// UEA path: deposit + autoswap into the UEA (if amount > 0), then execute payload via UEA
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					} else {
						// Non-UEA: check if recipient has code (smart contract) vs EOA
						codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
						if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
							isSmartContract = true
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L238-256)
```go
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

**File:** x/uexecutor/types/inbound.go (L150-164)
```go
	// Validate fields required per tx_type
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
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
