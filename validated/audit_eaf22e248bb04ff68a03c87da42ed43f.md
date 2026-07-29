### Title
Unauthorized cross-account UEA payload execution via `isCEA` inbound path bypasses owner-signature verification because `ExecutePayloadV2` is always called with the module account as `msg.sender` - ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
The C4 finding's root cause is a caller-context mismatch: a function that must authenticate its caller (`AdapterBase._verifyAndSetupStrategy`) was invoked with a call context that did not match what the callee assumed, defeating the authentication/interface assumption. Push Chain's `x/uexecutor` inbound-execution path has the same class of bug in reverse: the UEA contract's `executeUniversalTx` is documented to skip owner-signature verification specifically when `msg.sender == UNIVERSAL_EXECUTOR_MODULE` [1](#0-0) , and both inbound-execution keepers unconditionally call `ExecutePayloadV2` with the module account as `evmFrom` [2](#0-1) [3](#0-2) . For the `isCEA` inbound path, the `Recipient` field of the inbound is fully attacker-controlled (it can be set to any existing UEA address, not necessarily one owned by the source-chain `Sender`) [4](#0-3) , and the `UniversalPayload`/`VerificationData` fields are decoded directly from attacker-supplied source-chain event data with no ownership check tying `Recipient` to `Sender` [5](#0-4) [6](#0-5) .

### Finding Description
`x/uexecutor`'s authorization README explicitly documents the invariant that protects `MsgExecutePayload`: since `evmFrom != UNIVERSAL_EXECUTOR_MODULE`, "the contract enforces the signature check" [7](#0-6) . This wording implies the converse also holds inside `executeUniversalTx`: when the caller *is* the module account, the UEA contract does not require the same owner-signature verification (this is by design for the module-driven inbound-execution flow, in which the UV ballot process is meant to stand in for source-chain authorization).

Both inbound-execution keepers call:
```go
ueModuleAddr, _ := k.GetUeModuleAddress(ctx)
receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
``` [2](#0-1) [3](#0-2) 

which in turn issues `executeUniversalTx` on the UEA with the module as `from` via `DerivedEVMCall`/`CallUEAExecutePayload` [8](#0-7) .

For a non-`isCEA` inbound, `ueaAddr` is derived deterministically from `utx.InboundTx.Sender` via the factory (`CallFactoryToGetUEAAddressForOrigin`), so the module only ever calls `executeUniversalTx` on the UEA that legitimately belongs to that same source-chain sender — the invariant holds because `Sender` and `ueaAddr` are cryptographically linked.

For an `isCEA` inbound, however, `Recipient` is taken directly and unconditionally as the target UEA address, with only an "is this address a deployed UEA" check via `CallFactoryGetOriginForUEA` — there is **no check that `Recipient`'s owner corresponds to `Sender`** [9](#0-8) . Any external, unprivileged actor can call the source-chain Gateway contract with `isCEA=true`, `Recipient=<any deployed UEA on Push Chain>`, and an arbitrary `UniversalPayload.Data`/`RawPayload`. Honest Universal Validators will faithfully observe and vote for this real (attacker-initiated) source-chain event, since the ballot only verifies that the event actually happened on the source chain, not that the attacker is authorized to act on behalf of the named `Recipient`. Once the ballot passes, `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` executes the attacker-chosen payload against the victim's UEA with the module as `msg.sender`, which (per the documented invariant) causes the UEA to skip the owner signature check that would otherwise block execution.

### Impact Explanation
If confirmed against the actual `UEA_EVM.sol`/`UEA_SVM.sol` logic (external repo, not indexed here), this allows an unprivileged attacker to execute arbitrary calldata as any deployed UEA on Push Chain — e.g., `transfer`/`approve` calls on PRC20 tokens the victim UEA holds — without ever possessing the victim's private key or a valid signature. This is a direct "unauthorized UEA execution" / fund-draining primitive matching the in-scope impact category of stealing or unauthorized transfer of user-controlled funds through the universal execution path.

### Likelihood Explanation
The trigger requires no privileged role: any address can call the source-chain Gateway with `isCEA=true` and craft `Recipient`/payload fields. It only requires honest UV observation and voting (no malicious validator assumption needed), and the `isCEA` feature already ships as an intentional recipient-specified flow, making the missing sender/recipient binding highly likely to be reachable through the documented default `isCEA` code path.

### Recommendation
- In the `isCEA` branch, do not allow arbitrary `Recipient` UEAs to receive payload execution unless `UniversalPayload`/`VerificationData` still carries a signature that the UEA verifies against its own owner key regardless of caller identity (i.e., the module-sender bypass documented in the README should not extend to `isCEA` payload-execution calls).
- Alternatively, require that `isCEA` `Recipient` must equal the UEA deterministically derived from `Sender` (removing the arbitrary-recipient capability for payload execution), or restrict the CEA "recipient-not-owned-by-sender" case to fund-only deposits (no `executeUniversalTx` call) as already done for the "smart contract" and "EOA" branches.
- Confirm and, if necessary, patch the actual bypass condition inside `UEA_EVM.sol`/`UEA_SVM.sol` (in the separate `push-chain-core-contracts` repo) so `msg.sender == UNIVERSAL_EXECUTOR_MODULE` is not treated as sufficient proof of authorization for third-party-specified recipients.

### Proof of Concept
1. Attacker (no special role) calls the Gateway contract on a supported EVM/SVM source chain with `isCEA=true`, `recipient = <victim's deployed UEA address>`, `payload.data = <ERC20 transfer(attacker, victimBalance)>`, and `payload.to = <PRC20 token address held by victim UEA>`.
2. Honest Universal Validators observe this real (but attacker-initiated) event and vote it through `MsgVoteInbound`, since the vote only attests that the event occurred on-chain [10](#0-9) .
3. Ballot passes → `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` resolves `Recipient` as an existing UEA via `CallFactoryGetOriginForUEA` (no ownership check against `Sender`) [11](#0-10) .
4. `ExecutePayloadV2` is invoked with `evmFrom = ueModuleAddr` [2](#0-1) , so per the documented invariant the UEA's owner-signature check is bypassed, and the attacker's payload executes with the victim UEA's authority.

Note: I could not directly inspect `UEA_EVM.sol`/`UEA_SVM.sol` (hosted in the separate `push-chain-core-contracts` repository, not part of this indexed codebase) to confirm the exact bypass condition inside `executeUniversalTx`; the analysis above relies on the explicit invariant stated in `x/uexecutor/README.md`. Confirming the exact contract-level check is recommended before treating this as fully validated.

### Citations

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-80)
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-290)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L288-298)
```go
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

**File:** universalClient/chains/evm/event_parser.go (L254-272)
```go
func parseUniversalTx(event *store.Event, log *types.Log, dataOffset uint64, payload *common.UniversalTx, logger zerolog.Logger) {
	data := log.Data

	decodePayload(data, dataOffset, payload, logger)

	// revertRecipient (plain address at Word 3)
	if w := readWord(data, 3); w != nil {
		payload.RevertFundRecipient = ethcommon.BytesToAddress(w[12:32]).Hex()
	}

	// txType (Word 4)
	if w := readWord(data, 4); w != nil {
		payload.TxType = uint(new(big.Int).SetBytes(w).Uint64())
	}

	// signatureData (Word 5 offset)
	if w := readWord(data, 5); w != nil {
		payload.VerificationData = decodeSignatureData(data, w, uint64(32*7))
	}
```

**File:** x/uexecutor/types/decode_payload.go (L101-111)
```go
func DecodeRawPayload(rawPayload string, sourceChain string) (*UniversalPayload, error) {
	namespace := strings.Split(sourceChain, ":")[0]
	switch namespace {
	case "eip155":
		return DecodeUniversalPayloadEVM(rawPayload)
	case "solana":
		return DecodeUniversalPayloadSolana(rawPayload)
	default:
		return nil, fmt.Errorf("unsupported chain namespace for payload decoding: %s", namespace)
	}
}
```

**File:** x/uexecutor/keeper/evm.go (L155-193)
```go
// CallUEAExecutePayload executes a universal payload through UEA
func (k Keeper) CallUEAExecutePayload(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	universal_payload *types.UniversalPayload,
	verificationData []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiUniversalPayload, err := types.NewAbiUniversalPayload(universal_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

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
