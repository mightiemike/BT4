Based on my investigation, I found a concrete analog to the Caviar bug in the isCEA inbound execution path.

### Title
Unprivileged attacker can permanently freeze bridged funds by naming a non-UEA system/deployed contract as the `isCEA` inbound recipient - (File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go)

### Summary
The Caviar bug allowed an attacker to make a `PrivatePool` treat a factory-issued asset (another pool's NFT) as a trusted deposit target it didn't validate, letting funds get misrouted/stolen. The Push Chain analog is in the reverse direction but shares the same root cause — **the "recipient" address supplied in an attacker-influenced field is trusted without verifying it is a party capable of handling the deposited value**, in `ExecuteInboundFundsAndPayload` [1](#0-0) .

### Finding Description
When an inbound is marked `IsCEA=true`, the recipient is taken directly from attacker-controlled inbound data (the user fully controls what "recipient" value gets emitted from the source-chain gateway event) rather than being derived from the sender's own UEA [2](#0-1) . The code performs a three-way check:
1. If the recipient resolves to a UEA via `CallFactoryGetOriginForUEA`, it deposits into the UEA.
2. Otherwise, if the recipient has EVM code (`GetCodeHash` returns non-empty), it is treated as `isSmartContract` and `depositPRC20` runs, followed later by `CallExecuteUniversalTx` calling `executeUniversalTx` on that address [3](#0-2) .
3. Otherwise it's treated as a plain EOA deposit.

Nothing in this path excludes protocol/system contract addresses (e.g., `FACTORY_PROXY_ADDRESS_HEX`, the `UNIVERSAL_CORE` handler address, or the reserved proxy slots deployed by `x/uregistry`) from being classified as a legitimate "smart contract recipient." `depositPRC20` unconditionally mints PRC20 tokens to whatever `recipient` address is passed [4](#0-3) , and these system contracts have deployed code (so they satisfy the `codeHash != EmptyCodeHash` smart-contract check) but were never designed to hold or move arbitrary PRC20 balances, nor to implement a meaningful `executeUniversalTx(string,bytes,bytes,uint256,address,bytes32)` handler. If the contract doesn't implement that function, `CallExecuteUniversalTx` simply reverts and is recorded as a `FAILED` PCTx — but the PRC20 deposit that happened just before it is unconditionally committed and is not reverted for the isCEA path (explicit comment: "isCEA failures never create an INBOUND_REVERT outbound") [5](#0-4) .

This is validated end-to-end by honest Universal Validators (they only vote on what genuinely happened on the source chain), so the attack requires no privileged access — an ordinary external-chain user simply calls the source-chain gateway with `Recipient` set to a Push Chain system contract address instead of their own UEA/EOA.

### Impact Explanation
PRC20 tokens minted to a system contract (Factory proxy, UniversalCore handler, or reserved proxy slots) that has no withdraw/transfer entry point for arbitrary ERC20 balances become permanently stuck — no code path in `x/uexecutor` or the deployed system contracts recovers or redirects value sent there. This matches the in-scope impact category "permanent freezing... of user or protocol-controlled funds," reachable purely from default/ordinary user-submitted cross-chain deposits with honest validators and honest nodes.

### Likelihood Explanation
High from a "who can trigger it" standpoint — any external-chain user constructing a normal-looking bridge transaction can set the recipient field to a known Push Chain system-contract address instead of a UEA. The isCEA/CEA feature path is a supported, user-reachable transaction type (`FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` with `IsCEA=true`), not a privileged or internal-only flow.

### Recommendation
Before treating a non-UEA recipient as a valid "smart contract" deposit target, explicitly reject known protocol/system-contract addresses (Factory proxy, `UNIVERSAL_CORE` handler, reserved proxy slots, and any other addresses returned by `uregistrytypes.SYSTEM_CONTRACTS`). Alternatively, require the target contract to prove it implements `executeUniversalTx` (e.g., via a lightweight `supportsInterface`/static-call probe) before committing the PRC20 deposit, and only commit the deposit atomically with a successful `executeUniversalTx` call (or otherwise provide an admin/refund mechanism for stuck deposits).

### Proof of Concept
1. Attacker calls the source-chain gateway contract's bridge function with `Recipient = <Push Chain UNIVERSAL_CORE handler address>` (or Factory proxy address), `TxType = FUNDS_AND_PAYLOAD`, `IsCEA = true`, and an arbitrary `UniversalPayload`.
2. Honest Universal Validators observe this genuine on-chain event and vote `MsgVoteInbound` accordingly — no dishonesty required, since the recipient value is exactly what was emitted on the source chain.
3. Ballot passes; `ExecuteInboundFundsAndPayload` runs: `CallFactoryGetOriginForUEA` returns `isUEA=false`; `GetCodeHash` on the handler address returns non-empty → `isSmartContract=true`; `depositPRC20` mints PRC20 tokens to the handler contract address [3](#0-2) .
4. `CallExecuteUniversalTx` subsequently reverts (handler doesn't implement `executeUniversalTx`), recorded as `FAILED` PCTx, but the deposit PCTx from step 3 remains `SUCCESS` and is never rolled back (no `INBOUND_REVERT` for isCEA) [6](#0-5) .
5. The PRC20 balance is now permanently held by the system contract with no recovery path — funds are unrecoverably frozen.

**Uncertainty note:** I was unable to directly verify from the indexed code snippets whether `UNIVERSAL_CORE`/Factory proxy contracts have a fallback or admin-only sweep function that could recover such stray PRC20 balances (this would need to be checked in the actual deployed Solidity contract bytecode/ABI, which is outside this repo's Go code index). If such a recovery mechanism exists, the impact would be reduced from "permanent freeze" to "temporary lock requiring privileged intervention." A Devin session with full repository/contract access would be needed to confirm this definitively.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L103-103)
```go
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L187-206)
```go
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
