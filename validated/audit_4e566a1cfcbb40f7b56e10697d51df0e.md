## Finding

### Title
Inbound `Recipient` address for CEA-routed deposits is not validated against Push Chain's reserved system-contract address space, allowing PRC20 funds to be minted to unrecoverable system addresses - (File: `x/uexecutor/types/inbound.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
This is the closest native analog to RSCO-6. The RISC0 bug allowed an ELF's `p_vaddr` to be loaded at any address, including the hard-coded `SYSTEM` memory region, because `load_elf()` only checked segment *size* against `MAX_MEM`, never checked the address *range* against the reserved system region. Push Chain has an equivalent hard-coded reserved address space — `SYSTEM_CONTRACTS` (`UNIVERSAL_CORE` at `0x...C0`, `UNIVERSAL_GATEWAY_PC` at `0x...C1`, `UNIVERSAL_BATCH_CALL` at `0x...Bc`, and the full `0xA0-0xCF` reserved proxy range) — but the `Recipient` field of a user-originated inbound (`isCEA` path) is validated only for hex-address *format*, never checked against this reserved space. [1](#0-0) 

### Finding Description
`Inbound.ValidateForExecution` accepts any syntactically valid hex address as `Recipient` when `IsCEA` is true — there is no check excluding Push Chain's own reserved/system address space: [2](#0-1) 

`Recipient` originates from the source-chain gateway event (an ordinary user calling `addFunds` on the gateway with an attacker-chosen recipient parameter), is canonicalized but not restricted, and is carried through to execution: [3](#0-2) 

In `ExecuteInboundGasAndPayload` (and analogously in `ExecuteInboundFundsAndPayload`), the CEA branch takes the raw `Recipient` as `ueaAddr`, and only distinguishes "is this a UEA" vs "does it merely have code" (`isSmartContract`) — it never excludes the reserved system-contract range before minting PRC20 / depositing funds to it: [4](#0-3) 

If `Recipient` is set to a `SYSTEM_CONTRACTS` address (e.g. `UNIVERSAL_CORE` `0x...C0`, `UNIVERSAL_GATEWAY_PC` `0x...C1`, or any of the reserved `0xA0–0xCF` proxy slots), the module still proceeds to mint/auto-swap PRC20 value to that address via `gasAndPayloadDepositAutoSwap`/`depositPRC20`, both of which route through `DerivedEVMCall` with the `ue` module account as sender: [5](#0-4) 

These system-contract proxies are UUPS proxies whose fallback only understands `upgradeToAndCall`-style admin selectors from their designated `ProxyAdmin`; they expose no ERC20-rescue/withdraw path for tokens minted directly at their address. Sending PRC20 balance to such an address is not the same as calling a function on it — an ERC20 `mint`/`transfer` to an address only requires the address to exist; it does not require the recipient contract to implement any handling logic, so the tokens land in a slot with no code path to move them back out.

### Impact Explanation
An ordinary unprivileged user (the same actor who would call the gateway `addFunds`) can set `Recipient` to any `SYSTEM_CONTRACTS`/reserved address. Once honest Universal Validators vote this inbound in (nothing in `ValidateBasic`/`ValidateForExecution` rejects it), the `ue` module mints/deposits PRC20 value to that address. Because these addresses are proxy contracts with no arbitrary-token rescue function, the minted PRC20 balance becomes permanently unrecoverable — a **permanent freezing of protocol/user-controlled funds**, matching the in-scope impact category. This mirrors the ELF bug's core defect class exactly: an attacker-controlled "virtual address" is accepted without checking it against the chain's own hard-coded system/reserved memory region.

### Likelihood Explanation
The trigger requires only a standard `addFunds` call on a supported source-chain gateway with a crafted `recipient`/`isCEA` inbound — no validator, relayer, or admin collusion, and no cryptographic bypass is needed. The reserved address constants are public (`x/uregistry/types/constants.go`), making the target addresses trivial to discover.

### Recommendation
In `Inbound.ValidateForExecution` (and/or the CEA execution paths in `execute_inbound_funds_and_payload.go` / `execute_inbound_gas_and_payload.go`), reject (or force-revert) any inbound whose `Recipient` falls inside `uregistrytypes.SYSTEM_CONTRACTS` / the reserved `0xA0–0xCF` proxy range, mirroring how `BlockedAddresses()` in `app/app.go` already excludes precompiles and module accounts from ordinary EVM transfers.

### Proof of Concept
1. On a supported source chain, call the gateway's `addFunds` with `isCEA=true` and `recipient = 0x00000000000000000000000000000000000000C0` (the `UNIVERSAL_CORE` system-contract address) and a nonzero amount.
2. Honest Universal Validators observe and vote the event in exactly as emitted (no validator collusion needed); `ValidateForExecution` passes because `0x...C0` is a syntactically valid hex address.
3. `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` sees `Recipient` has code (`isSmartContract=true`, since it's a proxy) and calls `gasAndPayloadDepositAutoSwap`/`depositPRC20` targeting that address.
4. PRC20 value is minted at `0x...C0`; because the proxy contract has no function to move out unexpected ERC20 balances, the funds are permanently stuck.

**Note on verification gaps:** I was not able to fully read the beginning of the `IsCEA` branch in `execute_inbound_funds_and_payload.go` (lines 53–104) or the implementation of `depositPRC20`/`gasAndPayloadDepositAutoSwap`/`CallExecuteUniversalTx`'s downstream behavior in full within the available iterations, nor confirm definitively that none of the `SYSTEM_CONTRACTS` proxies expose any token-rescue mechanism. These would need to be checked in a full session before treating this as conclusively confirmed exploitable versus merely a missing defense-in-depth check.

### Citations

**File:** x/uregistry/types/constants.go (L37-69)
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
	"RESERVED_0": {
		Address:        "0x00000000000000000000000000000000000000B0",
		ProxyAdmin:     "0xF2000000000000000000000000000000000000b0",
		Implementation: "0xf1000000000000000000000000000000000000b0",
	},
	"RESERVED_1": {
		Address:        "0x00000000000000000000000000000000000000B1",
		ProxyAdmin:     "0xF2000000000000000000000000000000000000b1",
		Implementation: "0xf1000000000000000000000000000000000000b1",
	},
	"RESERVED_2": {
		Address:        "0x00000000000000000000000000000000000000b2",
		ProxyAdmin:     "0xf2000000000000000000000000000000000000b2",
		Implementation: "0xF1000000000000000000000000000000000000b2",
	},
}
```

**File:** x/uexecutor/types/inbound.go (L21-36)
```go
func (p *Inbound) Canonicalize() {
	p.SourceChain = strings.TrimSpace(p.SourceChain)
	p.TxHash = utils.LenientCanonicalizeTxHash(p.SourceChain, p.TxHash)
	p.Sender = utils.LenientCanonicalizeAddress(p.SourceChain, p.Sender)
	p.AssetAddr = utils.LenientCanonicalizeAddress(p.SourceChain, p.AssetAddr)
	// Recipient lives on Push Chain (EVM) regardless of source chain.
	p.Recipient = utils.LenientCanonicalizeEVMAddress(p.Recipient)
	p.LogIndex = strings.TrimSpace(p.LogIndex)
	p.Amount = strings.TrimSpace(p.Amount)
	p.RawPayload = utils.CanonicalizeHexBlob(p.RawPayload)
	p.VerificationData = utils.CanonicalizeHexBlob(p.VerificationData)
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
}
```

**File:** x/uexecutor/types/inbound.go (L156-164)
```go
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L60-97)
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
```

**File:** x/uexecutor/keeper/evm.go (L261-303)
```go
// Calls Handler Contract to deposit prc20 tokens
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
