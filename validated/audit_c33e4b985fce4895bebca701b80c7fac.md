## Finding: `depositPRC20` doesn't validate that `NativeRepresentation.ContractAddress` is set, allowing calls to the zero address

### Title
Missing ContractAddress validation in `depositPRC20` lets a `TokenConfig` with a denom-only (or empty) `NativeRepresentation` route funds to the zero address instead of a valid PRC20 mint target - (File: `x/uregistry/types/native_representation.go`, `x/uexecutor/keeper/handler.go`)

### Summary
`NativeRepresentation.ValidateBasic` treats an empty `ContractAddress` as fully valid whenever it's paired with any (or no) `Denom` value: [1](#0-0) 

This means a `TokenConfig` that only sets `Denom` (no `ContractAddress`) passes `TokenConfig.ValidateBasic` cleanly: [2](#0-1) 

However, the deposit-execution path (`depositPRC20`, used for `FUNDS`, `GAS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD` inbound tx types) only guards against a fully-nil `NativeRepresentation`, never against an empty `ContractAddress`: [3](#0-2) 

If `ContractAddress == ""`, `common.HexToAddress("")` silently resolves to `0x0000000000000000000000000000000000000000`, and `CallPRC20Deposit` issues a `DerivedEVMCall` invoking `depositPRC20Token(zeroAddress, amount, recipient)` on the `UNIVERSAL_CORE` handler contract: [4](#0-3) 

### Impact Explanation
An honest admin can register a `TokenConfig` with a `Denom`-only `NativeRepresentation` (the code and its own tests explicitly treat this as a supported/"optional" combination — see `x/uregistry/types/native_represenation_test.go`), yet the same `TokenConfig` is fully eligible for the PRC20-deposit path when an inbound event for that `chain:address` is voted through. Once a legitimate, unprivileged user deposits on the external gateway and honest Universal Validators reach quorum (the ordinary `MsgVoteInbound` flow), `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload` calls `depositPRC20` with a zero-value `prc20AddressHex`, dispatching an EVM call to the zero address rather than a real PRC20 contract.

Whether this results in silent fund loss (the on-chain success/failure of `PcTx` in this case, e.g. `execute_inbound_funds.go`) depends on the exact bytecode of the `UniversalCore` Solidity handler's `depositPRC20Token` implementation, which is not present in the scoped/indexed code available to me — I could not locate `UniversalCore.sol` or its `depositPRC20Token` function body in this repository to confirm whether a call targeting an address with no code reverts (typical for EVM low-level calls without extcodesize checks, calls to code-less addresses return success with no data). If it does not revert, `err` in `ExecuteInboundFunds` remains `nil`, `PcTx.Status` is recorded as `"SUCCESS"`, and — critically — since success suppresses the `INBOUND_REVERT` refund path (see the `if err != nil && !inbound.IsCEA` gate in `execute_inbound_funds.go`), the user's principal is neither minted anywhere nor refunded: it is permanently lost. [5](#0-4) 

### Likelihood Explanation
This requires an admin-created `TokenConfig` with an empty `ContractAddress` (not necessarily malicious — the field is documented and tested as "optional"), followed by an ordinary, unprivileged user deposit referencing that token, processed by honest UVs through the standard voting flow. No malicious validator, UV, or admin intent is required — only a config-validation gap combined with normal deposit traffic.

### Recommendation
- In `NativeRepresentation.ValidateBasic`, or better, in `TokenConfig.ValidateBasic`, require `ContractAddress` to be non-empty whenever the token is eligible for PRC20-minting tx types (or simply always require it, since `depositPRC20` is unconditionally reachable for every registered token).
- In `depositPRC20` (`x/uexecutor/keeper/handler.go`), explicitly check `tokenConfig.NativeRepresentation.ContractAddress == ""` and return a typed error before calling `CallPRC20Deposit`, ensuring the deposit is recorded as `FAILED` pre-finalization and the standard `INBOUND_REVERT` refund path is triggered instead of dispatching an EVM call to the zero address.

### Proof of Concept
1. As admin, register a `TokenConfig` via `MsgAddTokenConfig` with `NativeRepresentation{Denom: "uusdc", ContractAddress: ""}` — this passes `ValidateBasic` (see `native_represenation_test.go` "valid - only denom set").
2. As an unprivileged user, deposit funds on the corresponding external gateway referencing that token/chain pair.
3. Honest UVs observe the event and vote via `MsgVoteInbound`; quorum is reached and `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload` executes.
4. `depositPRC20` resolves `prc20AddressHex = common.HexToAddress("")` = zero address and issues the EVM call.
5. Assert (currently unverified against actual `UniversalCore.sol` bytecode, which is outside indexed scope) whether `PcTx.Status` ends up `"SUCCESS"` with no PRC20 minted to any account — the condition the exploit question asks to rule out.

### Uncertainty note
I could not locate the `UniversalCore.sol` handler contract's `depositPRC20Token` implementation in the indexed codebase to confirm the exact EVM-level outcome (revert vs. silent success) of a call targeting the zero address. This determines whether the impact is "clean failure + refund" (benign) or "silent fund loss" (the reported vulnerability). If you need this confirmed against the actual contract bytecode, a Devin session with full repository/contract access would be required to inspect the Solidity source directly.

### Citations

**File:** x/uregistry/types/native_representation.go (L23-33)
```go
// ValidateBasic performs sanity checks on the NativeRepresentation
func (p NativeRepresentation) ValidateBasic() error {
	// If both fields are empty, that's allowed (optional native_representation)
	if strings.TrimSpace(p.Denom) == "" && strings.TrimSpace(p.ContractAddress) == "" {
		return nil
	}

	// If contract address is set, it must be a 0x-prefixed valid format (basic check)
	if p.ContractAddress != "" && !strings.HasPrefix(p.ContractAddress, "0x") {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "contract_address must start with 0x")
	}
```

**File:** x/uregistry/types/token_config.go (L60-65)
```go
	if p.NativeRepresentation == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "native_representation is required")
	}
	if err := p.NativeRepresentation.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid native representation")
	}
```

**File:** x/uexecutor/keeper/handler.go (L12-45)
```go
func (k Keeper) depositPRC20(
	ctx sdk.Context,
	sourceChain string,
	assetAddr string,
	recipient common.Address,
	amountStr string,
) (*vmtypes.MsgEthereumTxResponse, error) {
	// get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
	if err != nil {
		return nil, err
	}

	if tokenConfig.NativeRepresentation == nil {
		return nil, fmt.Errorf("token config for %s:%s has no native representation", sourceChain, assetAddr)
	}
	prc20Address := tokenConfig.NativeRepresentation.ContractAddress
	prc20AddressHex := common.HexToAddress(prc20Address)

	// convert amount
	amount := new(big.Int)
	amount, ok := amount.SetString(amountStr, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", amountStr)
	}

	k.Logger().Debug("EVM call: depositPRC20Token",
		"prc20", prc20AddressHex.Hex(),
		"recipient", recipient.Hex(),
		"amount", amountStr,
	)

	// call PRC20 deposit
	return k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)
```

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L24-86)
```go
	receipt, err := k.depositPRC20(
		sdkCtx,
		inbound.SourceChain,
		inbound.AssetAddr,
		common.HexToAddress(inbound.Recipient), // recipient is inbound recipient
		inbound.Amount,
	)

	if err != nil {
		k.Logger().Warn("execute inbound funds: deposit failed",
			"utx_key", utx.Id,
			"source_chain", inbound.SourceChain,
			"error", err.Error(),
		)
	} else {
		k.Logger().Info("execute inbound funds: deposit succeeded",
			"utx_key", utx.Id,
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
	}

	_, ueModuleAddressStr := k.GetUeModuleAddress(ctx)
	universalTxKey := types.GetInboundUniversalTxKey(*inbound)
	if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
		pcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
		}

		// Capture tx hash from receipt even on EVM revert -- the reverted tx
		// still has a valid hash for debugging via eth_getTransactionByHash.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}

		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
		} else {
			pcTx.Status = "SUCCESS"
		}

		utx.PcTx = append(utx.PcTx, &pcTx)
		return nil
	}); updateErr != nil {
		return updateErr
	}

	// isCEA failures never create an INBOUND_REVERT outbound
	// (consistent with execute_inbound_funds_and_payload.go and execute_inbound_gas_and_payload.go)
	if err != nil && !inbound.IsCEA {
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
		if attachErr := k.attachOutboundsToUtx(sdkCtx, utx.Id, []*types.OutboundTx{revertOutbound}, err.Error()); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, utx.Id, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```
