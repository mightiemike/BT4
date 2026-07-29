### Title
Unbound `Recipient` in isCEA payload-execution flow lets any depositor direct module-executed `executeUniversalTx` calls at an arbitrary victim UEA - ([File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
`ExecuteInboundGasAndPayload` and `ExecuteInboundFundsAndPayload` both implement an `IsCEA` fast-path where the payload's target address (`utx.InboundTx.Recipient`) is taken directly from attacker-controlled source-chain event data and is *not* required to correspond to the UEA of `utx.InboundTx.Sender`. When that `Recipient` happens to be *any* already-deployed UEA on Push Chain (not necessarily the depositor's own), the keeper still deposits funds into it and then unconditionally calls `k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)` using the trusted `uexecutor` module account as `evmFrom`, with `UniversalPayload` and `VerificationData` both fully attacker-supplied. This is structurally the same class of bug as the LiFi `GenericBridgeFacet`/`LibSwap` report: a protocol-trusted caller performs an EVM call whose target and calldata are shaped by an unprivileged input, with no binding to the intended beneficiary.

### Finding Description
In the `IsCEA` branch of both `ExecuteInboundGasAndPayload` (lines 61–99) and `ExecuteInboundFundsAndPayload` (lines 53–102), `Recipient` is read straight off the inbound event and checked only for "is this a deployed UEA" via `CallFactoryGetOriginForUEA` [1](#0-0) , not for ownership by `utx.InboundTx.Sender`. This is confirmed intentional by an existing integration test that explicitly documents "PRC20 balance lands at explicitly passed recipient even when recipient is not the sender's UEA" [2](#0-1) .

After the deposit, execution falls through to Step 6, which calls `ExecutePayloadV2` on that same `ueaAddr` using the module account as sender, passing the attacker-controlled `UniversalPayload`/`VerificationData` verbatim [3](#0-2)  and [4](#0-3) . `ExecutePayloadV2` in turn issues `CallUEAExecutePayload`, a `DerivedEVMCall` with `isModuleSender=false` but `from = ueModuleAddr` (the well-known `UNIVERSAL_EXECUTOR_MODULE` address, `0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`) [5](#0-4) .

The repository's own `x/uexecutor/README.md` documents that the UEA contract's authorization model treats `msg.sender == UNIVERSAL_EXECUTOR_MODULE` as a distinguished case, explicitly reasoning that a third-party `MsgExecutePayload` submission is only safe *because* `evmFrom != UNIVERSAL_EXECUTOR_MODULE` forces the normal owner-signature check to apply [6](#0-5) . This implies the on-chain UEA logic (in the separate `push-chain-core-contracts` repository, not present in this codebase) special-cases calls originating from the module address. The Go-layer keeper code contains no additional check binding `Recipient`/`ueaAddr` to `utx.InboundTx.Sender` before making that trusted, module-sender call, so if the UEA's contract-side trust of `UNIVERSAL_EXECUTOR_MODULE` relaxes the owner-signature requirement for that sender (as the README's reasoning suggests it must, to explain why the comparison matters at all), any unprivileged user who can trigger a cross-chain deposit event (an ordinary, permissionless action) can choose `Recipient` = any other user's already-deployed UEA and `UniversalPayload.To/Data` = arbitrary calldata, causing the module to execute that call *as* the victim's UEA.

### Impact Explanation
If the UEA contract does relax signature enforcement for `msg.sender == UNIVERSAL_EXECUTOR_MODULE` (the scenario the node's own documentation is built around), this allows unauthorized execution of arbitrary calls from a victim's Universal Executor Account — including token `transfer`/`approve` of any assets held by that UEA, effectively unauthorized UEA execution and theft of user funds, matching the "unauthorized UEA execution" and "stealing/draining of user funds" impact categories in scope.

### Likelihood Explanation
Triggering the `IsCEA=true` inbound path requires only a normal, permissionless cross-chain deposit (calling the source-chain gateway's `addFunds`-style method with attacker-chosen `Recipient`/payload fields) — no privileged role, validator collusion, or key compromise is needed; Universal Validators are expected to relay whatever the source-chain event contains, and quorum validators are honest in this threat model. The only gating factor for actual exploitability is whether the corresponding Solidity UEA contract genuinely relaxes signature verification when `msg.sender == UNIVERSAL_EXECUTOR_MODULE`; that contract is not part of this repository, so this cannot be fully confirmed from the node code alone.

### Recommendation
In the `IsCEA` branch of both `ExecuteInboundGasAndPayload` and `ExecuteInboundFundsAndPayload`, bind `Recipient` to the inbound's `Sender`/`UniversalAccountId` before allowing module-sender payload execution (i.e., only allow this fast path when `Recipient` resolves to the sender's own UEA, or otherwise require a valid owner signature independent of `msg.sender`). At minimum, verify from the `push-chain-core-contracts` UEA implementation whether `UNIVERSAL_EXECUTOR_MODULE` truly bypasses per-payload owner-signature verification, and if so, gate that bypass on an explicit sender-ownership check performed here in the keeper before issuing the derived call.

### Proof of Concept
1. Victim deploys/owns a UEA at address `V` on Push Chain.
2. Attacker (unprivileged) submits a deposit on any supported source chain calling the gateway with `IsCEA=true`, `Recipient=V`, `TxType=GAS_AND_PAYLOAD` (or `FUNDS_AND_PAYLOAD`), and a `UniversalPayload{To: <attacker-chosen token>, Data: <transfer/approve calldata favoring attacker>}` plus arbitrary `VerificationData`.
3. Honest Universal Validators observe and vote the inbound to quorum as usual; `ExecuteInboundGasAndPayload` executes: it verifies `V` is *a* UEA (not that it belongs to attacker), deposits funds into `V`, then calls `ExecutePayloadV2(ctx, ueModuleAddr, V, attackerPayload, attackerVerificationData)` [7](#0-6) .
4. If the UEA contract accepts calls from `UNIVERSAL_EXECUTOR_MODULE` without independently validating that the attacker-supplied payload was authorized by `V`'s actual owner, the attacker's calldata executes with `V` as `msg.sender`, exfiltrating any approved/held assets.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L67-83)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L286-298)
```go
	// --- deposit successful (or skipped for zero amount) → continue with payload

	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 6: execute payload
	k.Logger().Debug("executing payload via UEA (gas+payload)", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	receipt, err = k.ExecutePayloadV2(
		ctx,
		ueModuleAddr,
		ueaAddr,
		utx.InboundTx.UniversalPayload,
		utx.InboundTx.VerificationData,
	)
```

**File:** test/integration/uexecutor/inbound_cea_payload_test.go (L568-574)
```go
	t.Run("PRC20 balance lands at explicitly passed recipient even when recipient is not the sender's UEA", func(t *testing.T) {
		// Setup deploys a UEA for testAddress (person A).
		// This test sends an inbound whose Sender is TargetAddr2 (person B, no UEA deployed).
		// Recipient is person A's UEA — a UEA that has no relation to person B.
		// After execution the PRC20 balance must be at the recipient (person A's UEA), proving
		// that CEA routing is driven purely by the explicit recipient field, not by the sender's identity.
		prc20ABI, err := uexecutortypes.ParsePRC20ABI()
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-290)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-53)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```

**File:** x/uexecutor/README.md (L229-237)
```markdown
#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```
