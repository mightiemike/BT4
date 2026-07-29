### Title
Missing PRC20 approval before Universal Core deposit causes inbound deposit reverts - (File: `x/uexecutor/keeper/evm.go`)

### Summary
`CallPRC20Deposit` and `CallPRC20DepositAutoSwap` invoke `depositPRC20Token` / `depositPRC20WithAutoSwap` on the `UNIVERSAL_CORE` handler contract without first approving the handler to spend the universal-executor module account's PRC20 balance. Because the handler—not the module—is the `msg.sender` when it reaches the PRC20 contract, a `transferFrom`-style pull will check `allowance[module][handler]`, find zero, and revert with `LowAllowance`. Every inbound fund/gas deposit that routes through these helpers will therefore fail and be recorded as a failed PCTx, often spawning an incorrect `INBOUND_REVERT` outbound.

### Finding Description
In `x/uexecutor/keeper/evm.go`, `CallPRC20Deposit` builds a `DerivedEVMCall` from the module account (`ueModuleAccAddress`) to the `UNIVERSAL_CORE` handler and calls `depositPRC20Token(prc20, amount, to)` [1](#0-0) . `CallPRC20DepositAutoSwap` does the same for `depositPRC20WithAutoSwap` [2](#0-1) . Neither function issues an `approve` call on the PRC20 token before the deposit. The PRC20 ABI exposes `approve`, `allowance`, and a `LowAllowance` error [3](#0-2) [4](#0-3) , indicating that the PRC20 `deposit` path enforces an allowance check. Since the handler contract is the caller into PRC20, the required allowance is `allowance[ueModuleAccAddress][handler]`, which the executor never sets.

### Impact Explanation
An unprivileged user can trigger the failure by submitting any inbound deposit that reaches `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGas`, or `ExecuteInboundGasAndPayload`. Each of those paths calls `depositPRC20` [5](#0-4) , which routes to `CallPRC20Deposit` or `CallPRC20DepositAutoSwap`. When the handler's PRC20 pull reverts, the keeper records a `FAILED` PCTx and, for non-CEA inbounds, attaches an `INBOUND_REVERT` outbound [6](#0-5) . This corrupts the canonical UniversalTx state (wrong execution status, wrong outbound) and denies the deposit service.

### Likelihood Explanation
High. The missing approval is on the common path for every PRC20-backed inbound deposit and autoswap. There is no one-time infinite-approval setup visible in the executor, registry, or ante code, and the module account has no private key to issue approvals outside of `DerivedEVMCall`.

### Recommendation
Before each `depositPRC20Token` or `depositPRC20WithAutoSwap` call, issue a `PRC20.approve(handler, amount)` via `DerivedEVMCall` from the module account, or set an infinite allowance to the handler once during system initialization. For `depositPRC20WithAutoSwap`, also ensure the swap router/quoter allowance is covered if the handler delegates the swap.

### Proof of Concept
1. User submits an inbound `FUND` UniversalTx for a token whose `NativeRepresentation` is a PRC20.
2. Finalization calls `ExecuteInboundFunds`, which calls `depositPRC20` with the inbound amount and recipient.
3. `depositPRC20` calls `CallPRC20Deposit(ctx, prc20Address, recipient, amount)`.
4. `CallPRC20Deposit` sends an EVM tx from `ueModuleAccAddress` to the `UNIVERSAL_CORE` handler calling `depositPRC20Token(prc20, amount, recipient)`.
5. The handler calls `PRC20.deposit(recipient, amount)`. Because `msg.sender` is the handler, PRC20 checks `allowance[ueModuleAccAddress][handler] == 0` and reverts with `LowAllowance`.
6. `ExecuteInboundFunds` catches the error, appends a `FAILED` PCTx, and builds an `INBOUND_REVERT` outbound, even though the user's source-chain funds were already locked.

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L542-593)
```go
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
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
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
}
```

**File:** x/uexecutor/types/abi.go (L534-550)
```go
      "name": "allowance",
      "inputs": [
        { "name": "owner", "type": "address", "internalType": "address" },
        { "name": "spender", "type": "address", "internalType": "address" }
      ],
      "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
      "stateMutability": "view"
    },
    {
      "type": "function",
      "name": "approve",
      "inputs": [
        { "name": "spender", "type": "address", "internalType": "address" },
        { "name": "amount", "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [{ "name": "", "type": "bool", "internalType": "bool" }],
      "stateMutability": "nonpayable"
```

**File:** x/uexecutor/types/abi.go (L839-843)
```go
    { "type": "error", "name": "CallerIsNotUniversalExecutor", "inputs": [] },
    { "type": "error", "name": "GasFeeTransferFailed", "inputs": [] },
    { "type": "error", "name": "InvalidSender", "inputs": [] },
    { "type": "error", "name": "LowAllowance", "inputs": [] },
    { "type": "error", "name": "LowBalance", "inputs": [] },
```

**File:** x/uexecutor/keeper/handler.go (L12-46)
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
