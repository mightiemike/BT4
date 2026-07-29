## Analysis: Analogous Push Chain Finding

The OptimismPortal bug is about an unprivileged caller being able to set an arbitrary/privileged contract as the call *target*, with no allow/deny-list check, causing funds to become permanently stuck. Push Chain has a directly analogous gap in the `isCEA` inbound-execution path: the attacker-controlled `Recipient` field is never checked against Push Chain's own reserved **system contract addresses**.

### Title
Unvalidated CEA `Recipient` allows permanent freezing of bridged PRC20 funds at reserved system-contract addresses - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
For `isCEA` inbounds, `Recipient` is attacker-controlled (it comes straight from the source-chain gateway event a user emits) and is only checked to be *any* well-formed hex address [1](#0-0) . Execution then branches purely on whether that address is a UEA or "has code" [2](#0-1) . Push Chain's own reserved system contracts (`UNIVERSAL_CORE` at `0x...C0`, `UNIVERSAL_GATEWAY_PC` at `0x...C1`, `UNIVERSAL_BATCH_CALL` at `0x...Bc`, plus 41 auto-reserved slots) are real deployed contracts with code from genesis [3](#0-2) , so they satisfy the "is a smart contract" branch and are never excluded as invalid recipients.

### Finding Description
When an inbound is `isCEA=true` and `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`, the keeper resolves `Recipient` and takes one of three paths: UEA, deployed-smart-contract, or EOA [4](#0-3) . In the "deployed smart contract" branch, PRC20 tokens are minted to `Recipient` **first** via `depositPRC20`, and only afterward is `CallExecuteUniversalTx` attempted against that same address [5](#0-4) [6](#0-5) . Nowhere in `ValidateForExecution` or in the CEA execution keeper is `Recipient` checked against `uregistrytypes.SYSTEM_CONTRACTS`. An attacker fully controls `Recipient` (it is decoded from the attacker's own source-chain gateway transaction, not attested independently), so they can set it to `UNIVERSAL_CORE` (`0x00000000000000000000000000000000000000C0`) or any other reserved system address. That address has code (deployed at genesis) and is not a UEA, so it takes the "smart contract" branch: PRC20 is minted to it unconditionally, before the subsequent `executeUniversalTx` call (which will simply revert with no matching selector, since `UniversalCore`'s ABI has no such function) is even attempted. Because minting already committed, and no known code path in this module can pull PRC20 balance back out of a system proxy contract that never implements a transfer-out entry point for arbitrary tokens sent to it accidentally, the deposited funds are permanently stranded.

This mirrors the OptimismPortal finding precisely: an unprivileged, ordinary user-initiated cross-chain flow lets the caller nominate a privileged/system contract as the execution target with no allow-list, and the resulting fund movement into that target is irreversible.

### Impact Explanation
This is in-scope as "permanent freezing of user or protocol-controlled funds," reachable via the default `isCEA` inbound submission path with honest validators/nodes (validators simply observe and vote the real, attacker-emitted source-chain event; nothing about the vote path is malicious or requires a compromised validator). Any user bridging funds through a CEA-style transfer whose `Recipient` resolves to one of Push Chain's reserved system-contract addresses has their bridged PRC20 permanently locked with no recovery path, and repeatable by anyone at will (denial of value, not just a one-off accident).

### Likelihood Explanation
High. No privileged actor is required — the attacker only needs to submit a normal cross-chain deposit event (as any external-chain sender would) with `isCEA=true` and `Recipient` set to a hard-coded, publicly known reserved address such as `0x00000000000000000000000000000000000000C0`. The three-way branch logic that decides UEA vs. smart-contract vs. EOA treats any address with code identically regardless of whether it's a legitimate dApp or Push Chain's own core infrastructure contract.

### Recommendation
Before entering the "smart contract" branch (and before minting), reject any `Recipient` that matches an entry in `uregistrytypes.SYSTEM_CONTRACTS` (proxy, admin, or implementation addresses), in both `ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload`, and ideally at `Inbound.ValidateForExecution` time so the failure is recorded as a `FAILED` PCTx (as is already done for malformed/empty recipients) rather than allowing a deposit to proceed:
```go
if _, reserved := uregistrytypes.SYSTEM_CONTRACTS_BY_ADDR[strings.ToLower(utx.InboundTx.Recipient)]; reserved {
    execErr = fmt.Errorf("recipient cannot be a reserved system contract address")
}
```

### Proof of Concept
1. Attacker triggers a gateway event on a supported source chain (e.g. `eip155:11155111`) for a `FUNDS_AND_PAYLOAD` transfer with `IsCEA=true` and `Recipient = "0x00000000000000000000000000000000000000C0"` (the `UNIVERSAL_CORE` system address).
2. Honest Universal Validators observe and vote `MsgVoteInbound` on this real event; ballot passes normally.
3. `ExecuteInboundFundsAndPayload` resolves `Recipient`: `CallFactoryGetOriginForUEA` returns `isUEA=false`; `GetCodeHash` returns non-empty (system contract has code from genesis) → `isSmartContract=true`.
4. `depositPRC20` mints the bridged PRC20 amount to `0x...C0` unconditionally.
5. `CallExecuteUniversalTx` then calls `executeUniversalTx(...)` on `0x...C0`; since `UniversalCore` has no such function, the call reverts and is recorded as a `FAILED` PCTx — but the PRC20 mint from step 4 already succeeded and is not rolled back.
6. Bridged funds are now permanently held at the `UNIVERSAL_CORE` system contract with no code path in the module (or, presumably, in the system contract itself) to retrieve them.

### Citations

**File:** x/uexecutor/types/inbound.go (L156-161)
```go
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-101)
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
```

**File:** x/uregistry/types/constants.go (L37-53)
```go
// SYSTEM_CONTRACTS holds all system contracts
var SYSTEM_CONTRACTS = map[string]ContractAddresses{
	"UNIVERSAL_CORE": {
		Address:        "0x00000000000000000000000000000000000000C0",
		ProxyAdmin:     "0xf2000000000000000000000000000000000000c0",
		Implementation: "0xF1000000000000000000000000000000000000c0",
	},
	"UNIVERSAL_BATCH_CALL": {
		Address:        "0x00000000000000000000000000000000000000Bc",
		ProxyAdmin:     "0xf2000000000000000000000000000000000000BC",
		Implementation: "0xF1000000000000000000000000000000000000Bc",
	},
	"UNIVERSAL_GATEWAY_PC": {
		Address:        "0x00000000000000000000000000000000000000C1",
		ProxyAdmin:     "0xF2000000000000000000000000000000000000C1",
		Implementation: "0xF1000000000000000000000000000000000000C1",
	},
```

**File:** x/uexecutor/keeper/evm.go (L646-692)
```go
// CallExecuteUniversalTx calls executeUniversalTx on a smart-contract recipient.
// This is used for isCEA inbounds whose recipient is a deployed contract (not a UEA).
func (k Keeper) CallExecuteUniversalTx(
	ctx sdk.Context,
	recipientAddr common.Address,
	sourceChain string,
	ceaAddress []byte,
	payload []byte,
	amount *big.Int,
	prc20AssetAddr common.Address,
	txId [32]byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	recipientABI, err := types.ParseRecipientContractABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse recipient contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		recipientABI,
		ueModuleAccAddress,
		recipientAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"executeUniversalTx",
		sourceChain,
		ceaAddress,
		payload,
		amount,
		prc20AssetAddr,
		txId,
	)
}
```
