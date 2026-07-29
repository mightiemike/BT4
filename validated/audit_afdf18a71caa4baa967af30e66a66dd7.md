## Analog Found: Missing Minimum Gas-Limit Enforcement on Gasless `MsgExecutePayload`

### Title
Zero/near-zero `UniversalPayload.GasLimit` lets an unprivileged account submit fee-free `MsgExecutePayload` calls that fully bypass gas-fee accounting - ([File: x/uexecutor/types/universal_payload.go](x/uexecutor/types/universal_payload.go))

### Summary
The Optimism bug's root cause is that `depositTransaction` never enforced a minimum L2 gas limit, so a caller could submit a "gasless" (`gasLimit=0`) deposit and consume L2/protocol resources without paying for them. Push Chain has a structurally identical gap in `MsgExecutePayload`: the user-controlled `UniversalPayload.GasLimit` field is validated only as "a non-negative integer" with no floor, is passed unmodified into the EVM-level `DerivedEVMCall`, and the only fee-accounting step (`DeductGasFeesFromReceipt`) is a hard no-op whenever the resulting receipt reports `GasUsed == 0`. Combined with the fact that `MsgExecutePayload` is on the Cosmos-level gasless allowlist (no SDK tx fee either), this lets any unprivileged account submit repeated fee-free calls that still exercise real keeper/EVM machinery.

### Finding Description
`UniversalPayload.ValidateBasic()` treats `GasLimit` like any other `uint256`-shaped string field — it only rejects negative or non-numeric values, never zero: [1](#0-0) 

That value flows straight into the EVM-level call with no re-derivation or minimum enforcement: [2](#0-1) 

Fee accounting for that call is entirely receipt-driven, and is explicitly a no-op the moment `GasUsed == 0`: [3](#0-2) 

`MsgExecutePayload` is reachable by "any" signer (the module deliberately does not enforce `Signer == EVM(Owner)`), and it sits on the gasless message-type allowlist, so the Cosmos-level `DeductFeeDecorator` and `MinGasPriceDecorator` also skip charging the submitter for the SDK transaction itself: [4](#0-3) [5](#0-4) 

Putting these together: an attacker crafts `MsgExecutePayload` with `UniversalPayload.GasLimit = "0"` (or another value low enough to make the derived EVM call revert with ~0 gas consumed). The message passes `ValidateBasic`, incurs no Cosmos SDK fee (gasless), and drives the keeper through UEA-address resolution, ABI parsing, and a real `DerivedEVMCall` invocation: [6](#0-5) 
Because `receipt.GasUsed == 0`, `DeductGasFeesFromReceipt` never touches the UEA's UPC balance — the entire request is processed at zero cost to the attacker, on both the Cosmos-fee layer and the UPC-accounting layer.

### Impact Explanation
This is the same structural defect as M-11: a state-changing, resource-consuming entry point with no enforced minimum gas commitment, letting an unprivileged caller spend node/keeper compute (UEA resolution, EVM dispatch, ABI decode) repeatedly without paying via either fee mechanism the protocol relies on. It does not directly steal funds, but it defeats the intended "gas is billed from the UEA's balance" invariant described in `x/uexecutor/README.md`, and provides a free, unprivileged, network-reachable spam vector against a message type that already bypasses normal Cosmos fee protections.

### Likelihood Explanation
High reachability: any account can submit `MsgExecutePayload` (no bonding/whitelist requirement, unlike vote messages), the `GasLimit` field is fully attacker-controlled decimal text, and no code path re-derives or floors it before being handed to the EVM call or before the fee no-op check.

### Recommendation
Enforce a protocol-defined minimum `GasLimit` in `UniversalPayload.ValidateBasic()` (or in `ExecutePayload`/`CallUEAExecutePayload` before dispatch), and stop treating `GasUsed == 0` as an automatic "skip billing" condition — distinguish a legitimately failed Go-level call (no EVM tx created, `receipt == nil`) from a submitted-but-starved EVM call that consumed the minimum intrinsic gas and should still be billed for that consumption.

### Proof of Concept
1. Attacker (any funded-or-unfunded Cosmos account) builds `MsgExecutePayload` with an arbitrary `UniversalAccountId` and `UniversalPayload{ GasLimit: "0", ... }`, and any (even invalid) `VerificationData`.
2. Submits the tx; since `MsgExecutePayload` is in the gasless allowlist, `MinGasPriceDecorator`/`DeductFeeDecorator` skip both min-fee and fee-deduction checks.
3. `ExecutePayload` resolves the UEA address, calls `CallUEAExecutePayload` with `gasLimit = big.NewInt(0)`; the derived EVM call fails immediately with `GasUsed == 0`.
4. `DeductGasFeesFromReceipt` returns `nil` without touching any UPC balance.
5. Repeat indefinitely at zero cost, against arbitrary target UEAs, consuming keeper/EVM-dispatch resources on every submission.

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

**File:** x/uexecutor/keeper/evm.go (L172-193)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
}
```

**File:** x/uexecutor/keeper/fees.go (L93-109)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
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
```

**File:** app/txpolicy/gasless.go (L14-49)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)

	msgs := tx.GetMsgs()
	if len(msgs) == 0 {
		return false
	}

	for _, msg := range msgs {
		switch m := msg.(type) {
		case *authz.MsgExec:
			// Only gasless if ALL inner messages are allowed
			for _, innerMsg := range m.Msgs {
				if !slices.Contains(GaslessMsgTypes, innerMsg.TypeUrl) {
					return false
				}
			}
		default:
			if !slices.Contains(GaslessMsgTypes, sdk.MsgTypeURL(msg)) {
				return false
			}
		}
	}
	return true
}
```

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L48-97)
```go
	factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

	// Step 2: Compute smart account address
	// Calling factory contract to compute the UEA address
	ueaAddr, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, evmFrom, factoryAddress, universalAccountId)
	if err != nil {
		return err
	}

	if !isDeployed {
		// only deploy if the UEA address has funds and not deployed yet
		ueaAccAddr := sdk.AccAddress(ueaAddr.Bytes())
		balance := k.bankKeeper.GetBalance(sdkCtx, ueaAccAddr, pchaintypes.BaseDenom)
		if balance.Amount.Sign() == 0 {
			k.Logger().Warn("execute payload rejected: UEA not deployed and has no balance",
				"chain", caip2Identifier,
				"owner", universalAccountId.Owner,
			)
			return fmt.Errorf("UEA is not deployed")
		}

		k.Logger().Info("auto-deploying UEA before execute (pre-funded address)",
			"uea", ueaAddr.Hex(),
			"balance", balance.Amount.String(),
			"chain", caip2Identifier,
			"owner", universalAccountId.Owner,
		)
		if _, err := k.DeployUEAV2(ctx, evmFrom, universalAccountId); err != nil {
			return errors.Wrapf(err, "failed to auto-deploy pre-funded UEA")
		}
	}

	k.Logger().Debug("executing payload via UEA",
		"uea", ueaAddr.Hex(),
		"chain", caip2Identifier,
		"from", evmFrom.Hex(),
	)

	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```
