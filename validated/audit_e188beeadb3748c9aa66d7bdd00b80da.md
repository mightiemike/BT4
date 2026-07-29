## Finding [1](#0-0) 

Yes — `msgServer.ExecutePayload` derives `evmFromAddress` purely from `msg.Signer` via `utils.GetAddressPair`, with **no check** that the resulting address doesn't collide with the reserved `UNIVERSAL_EXECUTOR_MODULE` address.

### Title
Attacker-controlled `Signer` bech32 address can be crafted to bech32-decode into the reserved `UNIVERSAL_EXECUTOR_MODULE` EVM address, spoofing a privileged module-sender identity on the UEA contract call - (File: `x/uexecutor/keeper/msg_server.go`, `utils/address.go`)

### Summary
`GetAddressPair` (used by `ExecutePayload`) converts `msg.Signer` to raw bytes via `ConvertAnyAddressToBytes`, which for a non-`0x` string simply calls `sdk.AccAddressFromBech32(addr)` and returns whatever bytes were bech32-encoded — with no semantic restriction on which 20 bytes those are. [2](#0-1) [3](#0-2) 

`MsgExecutePayload.ValidateBasic` only checks that `Signer` decodes as *a* valid bech32 address; it never checks the decoded bytes against any reserved/system address. [4](#0-3) 

The resulting `evmFromAddress` is then passed straight through as `from` (i.e. the EVM `msg.sender`) to `CallUEAExecutePayload` → `DerivedEVMCall`, with `isModuleSender` hardcoded to `false` (the flag only affects synthetic-signature logic, not the `from`/`msg.sender` value itself). [5](#0-4) 

The project's own documentation states that the UEA contract's safety argument for `Signer ≠ Owner` rests entirely on the assumption `evmFrom != UNIVERSAL_EXECUTOR_MODULE (0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7)`: [6](#0-5) 

This phrasing implies the UEA contract treats `msg.sender == UNIVERSAL_EXECUTOR_MODULE` as a privileged/module-originated call that is *not* subject to the normal signature check — a bypass condition the Go-side keeper never guards against.

### Impact Explanation
If the external UEA contract indeed special-cases `msg.sender == UNIVERSAL_EXECUTOR_MODULE` to skip owner-signature verification (as the README's safety reasoning implies), an attacker who bech32-encodes the raw bytes `0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7` as their `Signer` address would have `evmFromAddress` resolve to that exact reserved address. They could then submit `MsgExecutePayload` naming *any* victim's `UniversalAccountId`/UEA with arbitrary (even invalid) `VerificationData`, and the contract would execute the payload as if it came from the trusted module — enabling unauthorized execution/draining of the victim's UEA. This is unprivileged, user-reachable (gasless `MsgExecutePayload`), and would constitute unauthorized UEA execution / fund theft, squarely within the "Required Impacts" scope.

### Likelihood Explanation
Exploitability at the keeper layer is high and easily provable: `ConvertAnyAddressToBytes`/`GetAddressPair` place no restriction preventing this collision, and `ValidateBasic` doesn't block it either — any attacker can construct a bech32 string whose payload bytes equal the reserved address. The only uncertainty is whether the actual UEA Solidity contract (external `push-chain-core-contracts` repo, not present in this scoped codebase) truly implements a `msg.sender == UNIVERSAL_EXECUTOR_MODULE` bypass branch, or whether that address is merely mentioned defensively/documentation-only with the contract still requiring a valid signature regardless of sender. **This cannot be fully verified from the scoped repository**, since the UEA contract source is not part of `x/`, `precompiles/`, `app/ante`, or `universalClient/`.

### Recommendation
Regardless of the UEA contract's exact behavior, the keeper should defensively reject any `MsgExecutePayload`/`MsgMigrateUEA` whose derived `evmFromAddress` equals the reserved `UNIVERSAL_EXECUTOR_MODULE` address (or the actual `k.GetUeModuleAddress(ctx)` value), closing off this identity-spoofing vector at the Cosmos layer instead of relying solely on contract-side trust of `msg.sender`.

### Proof of Concept
1. Compute a bech32 string with HRP = the chain's configured account prefix and data payload = raw bytes `14 19 1E A5 4B 4C 17 6F CF 86 F5 1B 0F AC 7C B1 E7 1D F7 D7` (i.e. `UNIVERSAL_EXECUTOR_MODULE`).
2. Submit `MsgExecutePayload{ Signer: <that bech32 string>, UniversalAccountId: <victim's UEA>, UniversalPayload: <attacker payload>, VerificationData: <arbitrary/invalid bytes> }`.
3. Confirm `ms.ExecutePayload` computes `evmFromAddress == common.HexToAddress("0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7")` (verifiable purely from `utils.GetAddressPair`/`ConvertAnyAddressToBytes` logic in this repo).
4. Assert whether `CallUEAExecutePayload` succeeds without valid signature verification against the actual deployed UEA contract — this final step requires the external contract bytecode/source, which is outside this repo's scope and could not be directly confirmed here. [1](#0-0) [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** x/uexecutor/keeper/msg_server.go (L42-55)
```go
// ExecutePayload handles universal payload execution on the UEA.
func (ms msgServer) ExecutePayload(ctx context.Context, msg *types.MsgExecutePayload) (*types.MsgExecutePayloadResponse, error) {
	_, evmFromAddress, err := utils.GetAddressPair(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to parse signer address")
	}

	err = ms.k.ExecutePayload(ctx, evmFromAddress, msg.UniversalAccountId, msg.UniversalPayload, msg.VerificationData)
	if err != nil {
		return nil, err
	}

	return &types.MsgExecutePayloadResponse{}, nil
}
```

**File:** utils/address.go (L12-22)
```go
func ConvertAnyAddressToBytes(addr string) ([]byte, error) {
	if len(addr) == 0 {
		return common.Address{}.Bytes(), nil
	}

	if common.IsHexAddress(addr) {
		return common.FromHex(addr), nil
	}

	return sdk.AccAddressFromBech32(addr)
}
```

**File:** utils/address.go (L61-69)
```go
// get address pair returns both the cosmos and the 0x addresses, or an error
func GetAddressPair(addr string) (sdk.AccAddress, common.Address, error) {
	bz, err := ConvertAnyAddressToBytes(addr)
	if err != nil {
		return nil, common.Address{}, err
	}

	return sdk.AccAddress(bz), common.BytesToAddress(bz), nil
}
```

**File:** x/uexecutor/types/msg_execute_payload.go (L49-53)
```go
func (msg *MsgExecutePayload) ValidateBasic() error {
	// Validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}
```

**File:** x/uexecutor/keeper/evm.go (L178-193)
```go
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
