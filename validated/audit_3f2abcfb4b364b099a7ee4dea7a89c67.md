Confirmed: Push Chain's cosmos-evm fork wires the standard `bank`, `staking`, `distribution`, `gov`, `slashing`, `ics20` precompiles at fixed addresses via `app/precompiles.go` `NewAvailableStaticPrecompiles`, all of which use `msg.sender` to authorize the acting Cosmos account for transfers/delegations/votes.

### Title
Unwhitelisted, attacker-chosen `isCEA` recipient lets a contract impersonate the `uexecutor` module account via `DerivedEVMCall` — analog of unwhitelisted arbitrary external calls - ([File: x/uexecutor/keeper/evm.go])

### Summary
The external report flags MetaRouterV2 making external calls to arbitrary, non-whitelisted addresses (`firstDexRouter`, `secondDexRouter`, `relayRecipient`) with arbitrary calldata, letting a crafted callee abuse a pre-existing ERC20 approval. Push Chain's `x/uexecutor` module has the same architectural shape in its `isCEA` (contract-recipient) inbound path: the module issues a real, `msg.sender`-authenticated EVM call (`DerivedEVMCall`, `isModuleSender=true`) into an address supplied by the inbound event with no allow-list check beyond "not a UEA" and "has code."

### Finding Description
For `isCEA=true` inbounds, `ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload` set `ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)` directly from the observed inbound event [1](#0-0) , then check only whether that address is a known UEA or has bytecode [2](#0-1) . If it is an arbitrary attacker-deployed contract (not a UEA, has code), the module calls `CallExecuteUniversalTx`, which issues a `DerivedEVMCall` with `isModuleSender=true` and `from = ueModuleAccAddress`, targeting the attacker-chosen `recipientAddr` with attacker-influenced `payload` bytes [3](#0-2) . There is no whitelist of legitimate CEA recipient contracts — any address with code qualifies.

Because `DerivedEVMCall` produces a real EVM transaction where `msg.sender == ueModuleAccAddress` (the shared module account used for all module-originated PRC20 deposits, refunds, and gas top-ups) [4](#0-3) , an attacker-deployed contract at the CEA recipient address executes with that privileged `msg.sender`. Push Chain's cosmos-evm fork also activates stateful precompiles (`bank`, `staking`, `distribution`, `gov`, `slashing`, `ics20`) at fixed addresses that authorize the moving/staking/voting account based on `msg.sender` of the call reaching the precompile [5](#0-4) . A malicious contract invoked as the CEA recipient can `delegatecall` into these precompiles; a `delegatecall` preserves the outer `msg.sender` (`ueModuleAccAddress`) as seen by the precompile while running under the attacker's own contract logic, letting the attacker's code drive precompile calls (e.g. bank `Send`, staking `Delegate/Undelegate`, distribution `WithdrawDelegatorRewards`) that are authorized as if the `uexecutor` module account itself issued them — exactly the "arbitrary external call → abuse of an already-authorized principal" pattern from the MetaRouterV2 report, just substituting a Cosmos EVM precompile authorization check for an ERC20 `approve`/`transferFrom`.

### Impact Explanation
If `ueModuleAccAddress` (the `uexecutor` module account) holds any native `upc` balance, staked delegations, or unclaimed distribution rewards — plausible given it is the module account issuing gas top-ups/refunds and paying real EVM gas for every derived call — a crafted CEA-recipient contract can drain or redirect those funds/positions to an attacker-controlled address without any additional authorization, purely by being named as the `Recipient` of a real, honestly-observed source-chain inbound event with `IsCEA=true`. This falls squarely under "draining ... of protocol-controlled funds" and "unauthorized module-originated EVM execution" in the allowed-impact scope, and requires no privileged actor — only an ordinary user emitting a real gateway event with a chosen recipient contract.

### Likelihood Explanation
Moderate-to-high. Triggering the path requires no validator collusion: an unprivileged user can deploy any bytecode as the destination contract on the source chain event and set it as `Recipient` with `IsCEA=true`. Honest Universal Validators will faithfully observe and vote in the real event; the module will then unconditionally treat it as a valid CEA target since the only gating checks are "not a UEA" and "has code" [6](#0-5) . The remaining uncertainty is whether `ueModuleAccAddress` actually holds meaningful native balance/delegations in production — this was not directly confirmed in the indexed code and should be validated by a background agent inspecting genesis/module-account funding and any staking/distribution activity tied to that account.

### Recommendation
Whitelist the set of contracts eligible to receive `isCEA` module-originated calls (mirroring the report's recommendation to whitelist `firstDexRouter`/`secondDexRouter`/`relayRecipient`), e.g. via a registry entry per chain/token config analogous to `uregistry`'s `TokenConfig`/`ChainConfig`. At minimum, ensure the `uexecutor` module account never holds non-trivial native balance, delegations, or claimable rewards, and/or disable the `bank`/`staking`/`distribution`/`gov`/`slashing` precompiles for calls originating with `isModuleSender=true` senders, or restructure `DerivedEVMCall` module-sender transactions so `delegatecall` into stateful precompiles cannot observe the module account as `msg.sender`.

### Proof of Concept
1. Attacker deploys a malicious contract `M` on Push Chain whose `executeUniversalTx(...)` implementation ignores its arguments and instead does `address(BANK_PRECOMPILE).delegatecall(abi.encodeWithSignature("send(address,address,(string,uint256)[])", ueModuleAccAddress, attackerEOA, coins))` (or similarly targets staking/distribution precompiles).
2. Attacker submits a real deposit/gateway event on a supported source chain (e.g. Sepolia) specifying `Recipient = M` and `IsCEA = true`.
3. Honest Universal Validators observe this real event and submit `MsgVoteInbound` reflecting it exactly as it happened; 2/3+ quorum is reached honestly.
4. `x/uexecutor` executes the inbound: since `M` is not a UEA but has code, `isSmartContract=true`, and `CallExecuteUniversalTx` issues a `DerivedEVMCall` from `ueModuleAccAddress` into `M` [7](#0-6) .
5. During that call, `M`'s code runs with the outer `msg.sender = ueModuleAccAddress` preserved through its `delegatecall` into the bank/staking/distribution precompile, letting it move or claim funds attributed to the `uexecutor` module account to the attacker's address.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-62)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L70-98)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L238-248)
```go
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)
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

**File:** DERIVED_TRANSACTIONS.md (L106-131)
```markdown
### 2. Module-as-sender (protocol-initiated EVM work)

When `x/uexecutor` itself needs to issue an EVM call (deposit PRC20s, push chain-meta, refund unused gas, ...) the sender is the `uexecutor` module account. Module accounts don't have private keys, so this would be impossible via a normal `MsgEthereumTx` — you can't sign one. `DerivedEVMCall` with `isModuleSender=true` solves it:

```go
ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)
nonce, _ := k.GetModuleAccountNonce(ctx)
_, _ = k.IncrementModuleAccountNonce(ctx)

return k.evmKeeper.DerivedEVMCall(
    ctx,
    abi,
    ueModuleAccAddress, // module account as sender
    handlerAddr,
    big.NewInt(0),
    nil,
    true,               // commit
    false,              // gasless = false (we still want gas in the receipt)
    true,               // isModuleSender = true
    &nonce,             // manualNonce = explicit
    "depositPRC20Token",
    prc20Address, amount, to,
)
```

The fork is responsible for synthesising a deterministic "signature" for the module account so the tx can be properly receipted and indexed without ever needing a real key to exist.
```

**File:** app/precompiles.go (L94-146)
```go
	stakingPrecompile := stakingprecompile.NewPrecompile(
		stakingKeeper,
		stakingkeeper.NewMsgServerImpl(&stakingKeeper),
		stakingkeeper.NewQuerier(&stakingKeeper),
		bankKeeper,
		options.AddressCodec,
	)

	distributionPrecompile := distprecompile.NewPrecompile(
		distributionKeeper,
		distributionkeeper.NewMsgServerImpl(distributionKeeper),
		distributionkeeper.NewQuerier(distributionKeeper),
		stakingKeeper,
		bankKeeper,
		options.AddressCodec,
	)

	ibcTransferPrecompile := ics20precompile.NewPrecompile(
		bankKeeper,
		stakingKeeper,
		transferKeeper,
		channelKeeper,
	)

	bankPrecompile := bankprecompile.NewPrecompile(bankKeeper, erc20Kpr)

	govPrecompile := govprecompile.NewPrecompile(
		govkeeper.NewMsgServerImpl(&govKeeper),
		govkeeper.NewQueryServer(&govKeeper),
		bankKeeper,
		appCodec,
		options.AddressCodec,
	)

	slashingPrecompile := slashingprecompile.NewPrecompile(
		slashingKeeper,
		slashingkeeper.NewMsgServerImpl(slashingKeeper),
		bankKeeper,
		options.ValidatorAddrCodec,
		options.ConsensusAddrCodec,
	)

	// Stateless precompiles
	precompiles[bech32Precompile.Address()] = bech32Precompile
	precompiles[p256Precompile.Address()] = p256Precompile

	// Stateful precompiles
	precompiles[stakingPrecompile.Address()] = stakingPrecompile
	precompiles[distributionPrecompile.Address()] = distributionPrecompile
	precompiles[ibcTransferPrecompile.Address()] = ibcTransferPrecompile
	precompiles[bankPrecompile.Address()] = bankPrecompile
	precompiles[govPrecompile.Address()] = govPrecompile
	precompiles[slashingPrecompile.Address()] = slashingPrecompile
```
