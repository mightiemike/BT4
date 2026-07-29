## Analog Identified

Mapping the "push-transfer-can-fail-and-brick-recovery" bug class (M-1: repay() unconditionally `safeTransferFrom`s to a lender who can reject the transfer, with no pull-based fallback) onto Push Chain's universal execution path, the closest analog is in the **isCEA inbound execution flow**, where a failed `depositPRC20` deposit for a `isCEA=true` inbound is deliberately never reverted/refunded — funds are permanently unrecoverable through any user-reachable flow.

### Title
Failed PRC20 deposit on `isCEA` inbounds permanently strands bridged user funds with no revert path - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_funds.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
For inbounds marked `IsCEA` (Custom EVM Address / caller-specified recipient path), if the PRC20 deposit (mint) to the attacker/user-chosen recipient fails for any reason, the code path explicitly skips creating an `INBOUND_REVERT` outbound. This is the same root-cause shape as M-1: value is pushed unconditionally to a recipient address, and if that push fails, the protocol has no pull/refund mechanism to make the depositor whole.

### Finding Description
`ExecuteInboundFundsAndPayload` handles the `IsCEA` branch by calling `k.depositPRC20` directly against a caller-supplied `Recipient` address [1](#0-0) . When this deposit call fails, the code takes the `execErr != nil` branch but does **not** set `shouldRevert = true`, and the comment makes the design intent explicit: `// isCEA failures never create an INBOUND_REVERT outbound.` [2](#0-1) . The same comment/pattern recurs in `execute_inbound_funds.go` [3](#0-2)  and `execute_inbound_gas_and_payload.go` [4](#0-3) .

Concretely, `depositPRC20` looks up the PRC20 contract address from the registry token config and calls `CallPRC20Deposit`, which invokes `depositPRC20Token` on `UniversalCore` as a `DerivedEVMCall` [5](#0-4) [6](#0-5) . This is an on-chain EVM call whose success is not guaranteed: the underlying PRC20 `to` address is entirely user-controlled via the inbound `Recipient` field, and any revert in the mint/receive path (e.g., a malformed/incompatible contract at that address, a contract enforcing its own compliance/allow-list logic, or any other deterministic revert condition in the PRC20's mint hook) causes `depositPRC20` to return an error.

Once that happens for an `IsCEA` inbound, the flow records a `FAILED` `PCTx` and returns — permanently. There is no automatic outbound created to return the bridged asset to the original source-chain sender, unlike the non-CEA path, which does set `shouldRevert = true` and attaches an `INBOUND_REVERT` outbound in the same situation [7](#0-6) . The funds that were locked/burned on the external chain (per the inbound gateway event) are minted nowhere on Push Chain and are not scheduled to be returned — a state exactly analogous to the borrower's `debt` transfer failing against a blacklisted lender with no push-to-pull fallback.

### Impact Explanation
This breaks the "no permanent loss/freezing of user funds" invariant explicitly listed as in-scope. An ordinary unprivileged user who bridges funds via the `isCEA` path (a normal, user-reachable transaction flow — no privileged validator/relayer/admin action required to trigger it) can have their bridged value permanently stuck: the PCTx is marked `FAILED`, no `INBOUND_REVERT` outbound is ever attached, and the only path back to the user is a manually-triggered `RESCUE_FUNDS`/admin/governance flow which is out of scope of this report's guarantees (the user has no automatic on-chain recourse). This matches the "permanent freezing of user or protocol-controlled funds" impact bucket.

### Likelihood Explanation
The trigger requires only an ordinary, honest, unprivileged external-chain user submitting a standard `isCEA` inbound whose `Recipient` deposit call reverts — this can happen from something as mundane as specifying a recipient that is a contract incompatible with receiving the PRC20 mint call, without any malicious validator, relayer, or TSS participant involvement. Given `IsCEA` recipients are explicitly caller-supplied smart-contract or UEA addresses (per the three-way branch logic), a failing mint is a realistic, easily reachable condition, not a contrived edge case.

### Recommendation
Treat `IsCEA` deposit failures the same as the non-CEA path: set `shouldRevert = true` and attach an `INBOUND_REVERT` outbound (refunding to `RevertInstructions.FundRecipient` or the original sender) whenever `depositPRC20`/`gasAndPayloadDepositAutoSwap` fails for an `isCEA` inbound, removing the special-cased "never revert" comment/behavior in `execute_inbound_funds_and_payload.go`, `execute_inbound_funds.go`, and `execute_inbound_gas_and_payload.go`.

### Proof of Concept
1. An external-chain user submits a gateway deposit event with `IsCEA=true` and `Recipient` set to a contract address that will revert on the PRC20 mint call (e.g., a contract without appropriate fallback logic, or a contract whose bytecode makes it satisfy neither the UEA-check nor plain-EOA path safely).
2. Universal Validators reach quorum and vote `MsgVoteInbound`; `ExecuteInboundFundsAndPayload` runs, `depositPRC20` reverts, `execErr != nil`.
3. Because `IsCEA` is true, `shouldRevert` stays `false`; the function records a `FAILED` `PCTx` and returns without creating any `OutboundTx`.
4. `GetUniversalTx` for this UTX shows a `FAILED` PcTx entry and zero entries in `OutboundTx` of type `INBOUND_REVERT` — the bridged value is unrecoverable through any subsequent user-reachable message.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L59-102)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L103-103)
```go
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L144-205)
```go
			if execErr == nil && inboundAmount.Sign() > 0 {
				receipt, err = k.depositPRC20(
					sdkCtx,
					utx.InboundTx.SourceChain,
					utx.InboundTx.AssetAddr,
					ueaAddr,
					utx.InboundTx.Amount,
				)
				if err != nil {
					execErr = fmt.Errorf("depositPRC20 failed: %w", err)
					shouldRevert = true
					revertReason = execErr.Error()
				}
			}
		}
	}

	// --- record deposit attempt (only if amount > 0 or there was an error)
	if inboundAmount.Sign() > 0 || execErr != nil {
		depositPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			depositPcTx.TxHash = receipt.Hash
			depositPcTx.GasUsed = receipt.GasUsed
		}
		if execErr != nil {
			depositPcTx.ErrorMsg = execErr.Error()
		} else {
			depositPcTx.Status = "SUCCESS"
		}
		updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &depositPcTx)
			return nil
		})
		if updateErr != nil {
			return updateErr
		}
	}

	// If deposit failed, stop here.
	if execErr != nil {
		if shouldRevert {
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
		}
		return nil
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L74-86)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L60-100)
```go
		} else {
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
						// EOA: just deposit, skip executeUniversalTx
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					}
				}
				// isCEA failures never create an INBOUND_REVERT outbound.
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
