### Title
Missing `TokenConfig.Enabled` pause check allows PRC20 minting for disabled tokens - (File: x/uexecutor/keeper/execute_inbound_funds.go)

### Summary
`x/uregistry` defines a per-token `Enabled` flag on `TokenConfig` (the token-level equivalent of a mint pause), but the inbound execution paths in `x/uexecutor` that mint PRC20s from validated inbound events never check it. Every call site fetches the token config via `GetTokenConfig` and immediately proceeds to mint/deposit, so an inbound tied to a token an operator has disabled is still processed exactly like an enabled one — mirroring the reported `MintStarted`-missing-on-`mintTo` bug class where a pause flag exists but is not enforced on the value-moving function.

### Finding Description
`uregistry.TokenConfig` carries an `Enabled bool` field (analogous to a pausable "minting started/allowed" flag), set via `AddTokenConfig`/`UpdateTokenConfig`. [1](#0-0) 

By contrast, `x/uexecutor` enforces a *chain-level* enable flag rigorously: `VoteInbound` checks `IsChainInboundEnabled` before any state change [2](#0-1) , and `ExecutePayload` checks `chainConfig.Enabled.IsInboundEnabled` before doing any EVM work. [3](#0-2) 

However, none of the token-level minting paths perform the equivalent check on `TokenConfig.Enabled`. `ExecuteInboundFunds` calls `depositPRC20` directly using `inbound.AssetAddr` without first validating that the token is enabled: [4](#0-3) . The same pattern (fetch `TokenConfig` via `GetTokenConfig`, then proceed to deposit/mint without checking `.Enabled`) recurs in `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `handler.go`, all of which call `k.uregistryKeeper.GetTokenConfig(...)` [5](#0-4)  but never branch on the returned `Enabled` value (a global grep for `tokenConfig.Enabled` / `TokenConfig.Enabled` across the Go keeper code returns zero hits outside of generated protobuf/pulsar code).

Underlying mint mechanics: `depositPRC20`/`CallPRC20Deposit` is a module-originated `DerivedEVMCall` that mints PRC20 to the recipient with `isModuleSender=true`. [6](#0-5) 

### Impact Explanation
`TokenConfig.Enabled` is the operator-facing kill switch for a specific asset (e.g., to halt minting of a token under incident response, a bad price feed, a compromised bridge asset, or a deprecated mapping). Because the flag is never consulted on the inbound execution path, an ordinary user depositing funds on the source chain for a disabled token still causes honest Universal Validators to vote the inbound to quorum and the core validator to mint the PRC20 as usual — silently defeating the intended pause and continuing unauthorized/unintended minting of the “disabled” asset. This falls squarely under the in-scope impact "unauthorized mint ... of user or protocol-controlled funds," since operators cannot actually stop new PRC20 issuance for a token they've marked disabled through the mechanism apparently designed for that purpose.

### Likelihood Explanation
High reachability with zero attacker sophistication: any external, unprivileged user can trigger this simply by sending a normal cross-chain deposit for an asset that has been (or later is) marked `Enabled=false`, and honest, non-malicious validators will process it exactly as documented in the standard `VoteInbound` → `ExecuteInbound` → deposit flow. No validator collusion, no privileged access, and no crypto forgery are required — only the pre-existing absence of a guard clause that mirrors the missing `MintStarted` check in the source report.

### Recommendation
Add an explicit `tokenConfig.Enabled` check immediately after every `GetTokenConfig` call in the inbound execution keepers (`execute_inbound_funds.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `handler.go`), returning an error (and routing through the existing revert-outbound path, consistent with how token-config-lookup failures are already handled) before calling `depositPRC20`/`CallPRC20Deposit` or any other mint-adjacent `DerivedEVMCall`.

### Proof of Concept
1. Operator configures a token via `uregistry.AddTokenConfig` with `Enabled=false` for chain `eip155:X` / asset `A`, intending to halt further minting of PRC20 for `A`.
2. An unprivileged user sends a normal gateway deposit of asset `A` on chain `X`.
3. Honest Universal Validators observe the event and submit `MsgVoteInbound`; `VoteInbound` only checks `IsChainInboundEnabled` (chain-level), which is true, so the vote is accepted. [2](#0-1) 
4. On quorum, `ExecuteInbound` → `ExecuteInboundFunds` calls `k.depositPRC20(...)` using `inbound.AssetAddr` with no check against `tokenConfig.Enabled`. [4](#0-3) 
5. PRC20 for the “disabled” token `A` is minted to the recipient exactly as if the token were still enabled, confirming the pause has no effect on the mint path.

Note: I was unable to inspect the full Solidity/Go type-level documentation or comments describing the intended exact semantics of `TokenConfig.Enabled` (e.g., whether it's meant only to gate registry-level queries versus active mint suppression); this assessment is based on the field's naming, its usage pattern in tests, and confirmed absence of any runtime check in the keeper mint paths. Recommend a Devin session with full file access to confirm intended semantics from `x/uregistry/types` documentation/comments before finalizing the exact enforcement points.

### Citations

**File:** test/integration/uexecutor/chain_enabled_test.go (L59-72)
```go
	tokenConfig := uregistrytypes.TokenConfig{
		Chain:        "eip155:11155111",
		Address:      usdcAddress.String(),
		Name:         "USD Coin",
		Symbol:       "USDC",
		Decimals:     6,
		Enabled:      true,
		LiquidityCap: "1000000000000000000000000",
		TokenType:    1,
		NativeRepresentation: &uregistrytypes.NativeRepresentation{
			Denom:           "",
			ContractAddress: prc20Address.String(),
		},
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L38-46)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L24-30)
```go
	receipt, err := k.depositPRC20(
		sdkCtx,
		inbound.SourceChain,
		inbound.AssetAddr,
		common.HexToAddress(inbound.Recipient), // recipient is inbound recipient
		inbound.Amount,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L47-53)
```go
	// --- Step 1: token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
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
