### Title
CEA inbound path lets an attacker execute arbitrary calldata on any victim's UEA without owner authorization - (File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go)

### Summary
The Tapioca H-1 bug class is: an attacker-controlled "sender" identifier is decoupled from the account whose funds/authority is being exercised, and the downstream module trusts the caller instead of re-verifying that binding. Push Chain's CEA (contract-enabled-abstraction) inbound path reproduces this pattern: for `isCEA=true` inbounds, `Recipient` (an arbitrary victim UEA address) is used directly, and the payload is executed against that UEA via the `uexecutor` module account as `evmFrom`, which the UEA contract treats as a trusted caller and does **not** run its normal owner-signature check against.

### Finding Description
For ordinary (non-CEA) inbounds, the UEA that gets acted upon is derived deterministically from `UniversalAccountId{ChainNamespace, ChainId, Owner: utx.InboundTx.Sender}` [1](#0-0) , so the "sender" is cryptographically tied to the account that actually originated the source-chain event.

For `isCEA=true` inbounds, this binding is broken: `Recipient` is taken directly as the target UEA address, completely independent of `Sender`, and is only checked to be *a* deployed UEA (or a contract) — never checked against `Sender` or any cryptographic proof of ownership [2](#0-1) .

After the deposit/autoswap step, when the recipient is a UEA, the module proceeds to execute the attacker-supplied `UniversalPayload` against that UEA via `ExecutePayloadV2`, passing the `uexecutor` module account (`ueModuleAddr`) as `evmFrom`, not any address derived from the inbound `Sender`: [3](#0-2) 

Per the module's own documented authorization model, the UEA contract's owner-signature check is only enforced when `evmFrom != UNIVERSAL_EXECUTOR_MODULE`; the contract implicitly trusts calls that arrive as the module account: [4](#0-3) 

Combining these two facts: any external, attacker-controlled `isCEA=true` inbound event that names an arbitrary victim UEA as `Recipient` and supplies attacker-chosen `UniversalPayload.Data` will have that calldata executed against the victim's UEA by the trusted module sender, bypassing the UEA's owner/signature verification entirely. `VerificationData` in this flow is not required to correspond to anything — the integration tests explicitly exercise this with `VerificationData: ""` for `isCEA=true` GAS_AND_PAYLOAD inbounds targeting an existing UEA: [5](#0-4) 

This is the structural analog of the Tapioca H-1 issue: the "sender" identity attached to the cross-chain message (here, the CEA contract/event originator on the source chain) is never verified to be the same principal as the account whose state (the UEA) is being mutated, and the downstream execution module (`UEA_EVM`/`UEA_SVM` via `executeUniversalTx`) omits its normal authentication because the caller is a whitelisted/trusted module address — exactly mirroring how Magnetar's `_checkSender` in the original report let a whitelisted-but-unverified caller act on behalf of any user.

### Impact Explanation
An unprivileged attacker who can register or trigger an event on any chain/contract configured as a CEA source (a chain/token config that is `Enabled` for inbound) can craft an `Inbound` with `IsCEA=true`, `Recipient = <victim UEA address>`, and `UniversalPayload.Data = <arbitrary call, e.g. ERC20/PRC20 transfer, approve, or any function the victim's UEA can call>`. Once honest Universal Validators observe and vote (2/3+) on this genuine external-chain event, the payload executes on Push Chain against the victim's UEA with the module account as `msg.sender`, bypassing the UEA's cryptographic owner check. This allows unauthorized UEA execution and can be used to drain PRC20/native balances held by or reachable through the victim's UEA — directly matching the in-scope impact "unauthorized UEA or CEA execution" and "stealing... funds."

### Likelihood Explanation
The attacker only needs to originate a legitimate, observable event on any chain/contract enabled for inbound processing with `isCEA=true` — no compromise of validators, TSS, or governance is required, and honest validators voting on the attacker's own genuinely-emitted event is sufficient to finalize the ballot. This makes exploitation directly reachable from an ordinary unprivileged external actor, consistent with the "Allowed Impact Gate" (honest validators, unprivileged attacker, user-reachable flow).

### Recommendation
For `isCEA=true` inbounds whose recipient resolves to a deployed UEA, do not silently reuse the module-trusted `ExecutePayloadV2`/`UNIVERSAL_EXECUTOR_MODULE` bypass path. Either: (1) require and verify a real owner-bound signature/`verificationData` against the UEA's stored owner key before executing the payload (i.e., route through the same signature-checked path as the normal `MsgExecutePayload` flow), or (2) enforce that `Recipient` for isCEA UEA-targeted payload execution must correspond to `Sender`'s own derived UEA (mirroring the non-CEA path's binding), rejecting/failing the payload execution leg (while still allowing the deposit leg) when the recipient is a UEA the sender does not own and no valid owner signature is present.

### Proof of Concept
1. Attacker deploys/controls a contract on a chain that is registered and inbound-enabled in `x/uregistry` (or observes any enabled chain where an inbound-producing event can be freely triggered).
2. Attacker emits a source-chain event that a core validator decodes into an `Inbound` with: `IsCEA = true`, `Recipient = <victim's already-deployed UEA address>`, `TxType = GAS_AND_PAYLOAD` (or `FUNDS_AND_PAYLOAD`), `UniversalPayload.Data = <ABI-encoded call the attacker wants the victim UEA to perform>`, `VerificationData = ""`.
3. Honest Universal Validators observe this real event and submit `MsgVoteInbound` for it; once 2/3+ agree, the ballot passes and `ExecuteInboundGasAndPayload` runs.
4. Because `Recipient` is a valid UEA, execution reaches `ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)` at [6](#0-5) , with `evmFrom = ueModuleAddr` (the module account), so the UEA's owner-signature check is bypassed per the documented `evmFrom == UNIVERSAL_EXECUTOR_MODULE` trust exception.
5. The attacker's arbitrary `Data` executes against the victim's UEA, e.g. transferring out PRC20 balances the UEA holds, without any signature from the victim.

Note: I was not able to fully trace the currently-live implementation of the historical "verified payload hash" storage referenced in `app/upgrades/cea-payload-verification-fix/upgrade.go`, since the `x/utxverifier` module that appears related was removed in a later upgrade (`app/upgrades/remove-utxverifier/upgrade.go`). If that removal changed how/whether `VerificationData` is now enforced for CEA UEA-targeted payloads, it could affect the precise mechanics above; a Devin session with full repository access would be needed to confirm the exact current call graph for `VerificationData` validation in this path.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L31-35)
```go
	universalAccountId := types.UniversalAccountId{
		ChainNamespace: chainNamespace,
		ChainId:        chainId,
		Owner:          utx.InboundTx.Sender,
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L61-83)
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

**File:** test/integration/uexecutor/inbound_cea_gas_and_payload_test.go (L526-557)
```go
		// person B — a different sender on a different chain
		personBSender := utils.GetDefaultAddresses().TargetAddr2

		validUP := &uexecutortypes.UniversalPayload{
			To:                   ueaAddrHex.String(),
			Value:                "1000000",
			Data:                 "0xa9059cbb000000000000000000000000527f3692f5c53cfa83f7689885995606f93b616400000000000000000000000000000000000000000000000000000000000f4240",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		}

		// CEA inbound from eip155:97, but UEA origin is eip155:11155111
		ceaInbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:97",
			TxHash:           "0xceagas07",
			Sender:           personBSender,
			Recipient:        ueaAddrHex.String(),
			Amount:           "0",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_GAS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: personBSender,
			},
		}
```
