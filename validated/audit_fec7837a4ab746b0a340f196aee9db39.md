### Title
Unauthorized gas-fee drain of arbitrary Push Chain contracts via unsolicited `isCEA` inbound execution - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
An unprivileged external-chain attacker can name **any** already-deployed Push Chain smart contract as the `Recipient` of a source-chain gateway deposit with `IsCEA=true`. Once honest Universal Validators reach quorum on the observed event, `x/uexecutor` automatically deposits funds into that contract and calls `executeUniversalTx` on it, then unconditionally deducts a computed gas fee from the *recipient's own* native `upc` balance — even though the recipient never authorized, requested, or signed anything. This mirrors the Timeswap `borrow()` class of bug: attacker-influenced, unauthenticated input (`Recipient`, `UniversalPayload.Data/GasLimit/MaxFeePerGas`) drives a real state mutation (funds debited) against a party that never consented to the operation.

### Finding Description
In `ExecuteInboundFundsAndPayload` [1](#0-0) , when `utx.InboundTx.IsCEA` is true and the `Recipient` resolves to a non-UEA address with contract code, the keeper unconditionally treats it as a valid "CEA" (contract-originated-address) recipient and marks `isSmartContract = true` — there is no allowlist, opt-in registry, or capability check that the target contract has agreed to receive Push Chain crosschain calls.

The keeper then deposits PRC20 funds and calls the recipient via `CallExecuteUniversalTx` (module-originated `DerivedEVMCall`) [2](#0-1) , and if the EVM call does not itself revert (e.g., the contract has a non-reverting fallback/receive function, a permissive default handler, or simply ignores unknown selectors gracefully), it proceeds to `DeductGasFeesFromReceipt`, which computes `gasCost = baseFee * gasUsed` and burns that amount directly from the **recipient's** `upc` balance via `DeductAndBurnFees` [3](#0-2) .

Nothing in this path verifies that the `Recipient` address opted into being a CEA target, nor that the entity paying gas (the recipient) is the entity that initiated the crosschain transaction. `Sender`, `Recipient`, and the `UniversalPayload` fields (`GasLimit`, `MaxFeePerGas`, `MaxPriorityFeePerGas`, `Data`) all originate from the raw source-chain gateway event, which is fully attacker-controlled (any external-chain account can call the gateway contract naming any Push Chain address as recipient). The `x/uexecutor/README.md` documents the authorization model for the *UEA* path (`MsgExecutePayload`) as being enforced entirely inside the UEA contract's signature check [4](#0-3) , but the CEA smart-contract path has no equivalent authentication — the module simply trusts that `IsCEA=true` + "target has code" is sufficient to justify billing that target's balance.

### Impact Explanation
Any deployed contract on Push Chain that holds native `upc` and has a non-reverting fallback/receive path (a common pattern for wallets, proxies, and payment-accepting contracts) can be repeatedly targeted by an unprivileged attacker who only needs to submit cheap deposit transactions on any configured external chain. Each such inbound, once finalized by honest validators, silently drains real `upc` from the victim contract to pay for gas the victim never consented to spend. This is an unauthorized burn/drain of protocol-user funds reachable purely through the default inbound submission path — squarely within the "stealing, draining ... of user or protocol-controlled funds" and "unauthorized module-originated EVM execution" categories in scope.

### Likelihood Explanation
The trigger requires no privileged role — any external-chain account can originate the gateway deposit event that becomes the inbound observation. It requires only that honest, non-malicious validators observe and vote on a real (attacker-submitted) event, which is exactly the "unprivileged trigger with honest validators/nodes" scenario the scope calls out. Attack cost is bounded by a small external-chain gas fee and the (currently module-default, not attacker-set) EVM execution cost of a call to the victim contract.

### Recommendation
Require CEA recipients to explicitly opt in (e.g., via an on-chain registry keyed by contract address, or a well-known interface/marker the contract must implement and that the keeper checks before billing) before executing unsolicited `executeUniversalTx` calls against them and charging their balance. Alternatively, bill the gas cost against a protocol/relayer-funded account rather than the recipient's own balance for calls the recipient did not initiate, and/or require the recipient to pre-fund an explicit allowance for CEA billing rather than debiting its spendable balance directly.

### Proof of Concept
1. Attacker deploys nothing; instead identifies an existing Push Chain contract `V` that holds `upc` and has a fallback function that does not revert on unknown calldata (many wallets/proxies qualify).
2. Attacker submits a minimal-value deposit transaction on any configured external chain (e.g., Sepolia) through the Push Chain gateway contract, setting `recipient = V`, `isCEA = true`, and an arbitrary `UniversalPayload` (`Data` can be garbage, `GasLimit` high enough to not trip the `gasUsed > GasLimit` check in `DeductGasFeesFromReceipt`).
3. Honest Universal Validators observe this real event and submit `MsgVoteInbound`; once 2/3 quorum is reached, `ExecuteInboundFundsAndPayload` runs the flow at [5](#0-4) .
4. `CallExecuteUniversalTx` calls `V.executeUniversalTx(...)`; since `V`'s fallback does not revert, `contractErr == nil` and `DeductGasFeesFromReceipt` burns `baseFee * gasUsed` from `V`'s `upc` balance without `V`'s consent.
5. Attacker repeats step 2 cheaply and arbitrarily many times, progressively draining `V`'s native balance.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-88)
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-256)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

			amount := new(big.Int)
			amount, ok := amount.SetString(utx.InboundTx.Amount, 10)
			if !ok {
				contractErr = fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
			} else {
				txId := common.HexToHash(utx.Id)

				var payload []byte
				if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
					payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
				}

				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
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
			}
```

**File:** x/uexecutor/keeper/fees.go (L93-140)
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

**File:** x/uexecutor/README.md (L220-227)
```markdown
#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**
```
