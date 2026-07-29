## Analysis Result

I found a genuine analog to the `RateLimitedMinter` bug class: a protocol-defined economic safety parameter (`TokenConfig.LiquidityCap`) that is declared, validated, and documented but never enforced at the point of unauthorized-mint-equivalent action (PRC20 inbound minting).

### Title
`TokenConfig.LiquidityCap` is validated but never enforced, allowing unbounded PRC20 minting via ordinary user inbounds - (File: `x/uregistry/types/token_config.go`, `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`uregistry.TokenConfig` defines a `liquidity_cap` field intended as "max supply cap for this token" [1](#0-0) , and `ValidateBasic` requires it to be a non-empty string on every `MsgAddTokenConfig`/`MsgUpdateTokenConfig` [2](#0-1) . However, no keeper in `x/uexecutor` (the module that actually mints PRC20 on inbound) ever reads or checks this field — `depositPRC20` and `CallPRC20Deposit` mint the exact attacker-reported inbound `amount` unconditionally.

### Finding Description
The inbound → mint path is:
1. `VoteInbound` finalizes the ballot after honest UV quorum [3](#0-2) .
2. Execution calls `depositPRC20`, which resolves the `TokenConfig` for the source chain/asset and calls `CallPRC20Deposit` with the raw amount, with **no cap check** against `tokenConfig.LiquidityCap` [4](#0-3) .
3. `CallPRC20Deposit` issues a `DerivedEVMCall` to `depositPRC20Token` on the Handler/UniversalCore contract, minting the PRC20 to the recipient with no supply check performed on the Cosmos side [5](#0-4) .

A search across `x/uexecutor/**` for `LiquidityCap`/`SupplyCap`/`MaxSupply` returns zero matches, and no `TotalSupply` accounting exists in the keeper layer — the only `TotalSupply` references found are in unrelated ABI/test-helper code. This mirrors the `SimplePSM`/`RateLimitedMinter` finding precisely: a safety parameter (`hardCap`/`liquidity_cap`) is declared in the config layer but the mint entrypoint that a normal user's inbound event can trigger never consults it, so the parameter provides no actual protection.

### Impact Explanation
Per-token liquidity caps are the chain's only declared mechanism to bound how much synthetic PRC20 representation of an external asset can exist on Push Chain relative to real backing/registry expectations (used across every token config in `config/testnet-donut/**`, e.g. `liquidity_cap: "1000000000000000000000000"`). Because this cap is never checked in the mint path, an attacker who can generate legitimate-looking inbound gateway events (subject to normal UV vote finality — not an attacker forging votes, but an attacker legitimately depositing on the source chain and having honest UVs vote it in per protocol design) can mint PRC20 supply on Push Chain without any bound, exactly as Alice minted unlimited gUSDC through `SimplePSM`. Any downstream logic (swap quoting, gas-token selection, protocol accounting, or future governance/veto mechanisms keyed on PRC20 balances) that assumes `liquidity_cap` is an enforced invariant is operating on a false assumption. This is a corruption of PRC20 accounting semantics: the registry advertises a cap that does not exist in practice.

### Likelihood Explanation
High confidence that the code path exists and lacks a cap check (confirmed by grep across the full `x/uexecutor` module and inspection of `depositPRC20`/`CallPRC20Deposit`). Confidence is more moderate on the exact severity/exploitability because:
- Triggering it requires an unprivileged user to actually deposit real value on the external chain (as with the original PSM finding, this is not "free" mint — the attacker must post real collateral on the source chain), so it is not a costless drain, similar to how the original C4 finding was downgraded from High to Medium because "no user funds are at risk."
- Whether any current on-chain consumer (governance weight, veto quorum, etc.) depends on PRC20 total supply staying under `liquidity_cap` was not confirmed in this codebase — Push Chain does not appear to have an analogous `GuildVetoGovernor`-style veto mechanism gated by PRC20 balance in the code reviewed. Without such a consumer, the missing check is a real broken invariant but its concrete "unauthorized state transition" impact (per the allowed-impact gate) is less immediately demonstrable than in the original PSM case.

### Recommendation
Enforce `TokenConfig.LiquidityCap` in the uexecutor mint path: before calling `CallPRC20Deposit`/`CallPRC20DepositAutoSwap`, query the PRC20 contract's current total supply (or maintain a Cosmos-side running total per token) and reject/cap the inbound execution if `currentSupply + amount > LiquidityCap`, mirroring the `RateLimitedMinter.hardCap` enforcement pattern the audit report recommended in `LendingTerm`. At minimum, document/deprecate the field if it is intentionally unused, to avoid the "declared but dead" safety-parameter gap that caused the original finding.

### Proof of Concept
Not independently executable in ask-only mode — no runtime access. The static evidence is:
1. `x/uregistry/types/token_config.go:56-58` requires `LiquidityCap` to be set for every registered token.
2. `x/uexecutor/keeper/handler.go:12-46` (`depositPRC20`) reads `tokenConfig` but only checks `NativeRepresentation != nil`, never `LiquidityCap`.
3. `x/uexecutor/keeper/evm.go:262-303` (`CallPRC20Deposit`) mints the raw `amount` unconditionally via `depositPRC20Token`.
4. `grep -r "LiquidityCap|SupplyCap|MaxSupply" x/uexecutor/` returns no results, confirming no enforcement exists anywhere in the execution module.

A background Devin agent with repo/test access could write an integration test (following the pattern in `test/integration/uexecutor/inbound_zero_amount_test.go`) that: registers a `TokenConfig` with a small `LiquidityCap`, submits an inbound with `Amount` far exceeding that cap through the normal `MsgVoteInbound` quorum flow, and asserts the mint succeeds anyway — confirming the cap is not enforced.

### Citations

**File:** proto/uregistry/v1/types.proto (L130-145)
```text
message TokenConfig {
  option (amino.name) = "uregistry/token_config";
  option (gogoproto.equal) = true;
  option (gogoproto.goproto_stringer) = false;

  string chain = 1;                        // Chain ID in CAIP-2 format (e.g., eip155:1
  string address = 2;                      // Token address on external chain
  string name = 3;                         // Full token name (e.g., USD Coin)
  string symbol = 4;                       // Ticker (e.g., USDC)
  uint32 decimals = 5;                     // Number of decimals (e.g., 6 or 18)
  bool enabled = 6;                        // Whether this token is enabled for minting/bridging
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
  TokenType token_type = 8;                // Type of the token (e.g., ERC20, ERC721, ERC1155)

  NativeRepresentation native_representation = 9; // Native representation on the chain
}
```

**File:** x/uregistry/types/token_config.go (L56-58)
```go
	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L18-52)
```go
func (k Keeper) VoteInbound(ctx context.Context, universalValidator sdk.ValAddress, inbound types.Inbound) error {
	// Canonicalize first so every derived key + the stored inbound use one
	// representation per logical event.
	inbound.Canonicalize()

	k.Logger().Info("vote inbound received",
		"validator", universalValidator.String(),
		"source_chain", inbound.SourceChain,
		"tx_hash", inbound.TxHash,
		"tx_type", inbound.TxType.String(),
		"sender", inbound.Sender,
	)

	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Step 1: Derive UTX key from the original inbound data (source_chain:tx_hash:log_index)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)
	found, err := k.HasUniversalTx(ctx, universalTxKey)
	if err != nil {
		return errors.Wrap(err, "failed to check UniversalTx")
	}
	if found {
		k.Logger().Warn("vote inbound rejected: utx already exists", "utx_key", universalTxKey)
		return fmt.Errorf("universal tx with key %s already exists", universalTxKey)
	}
```

**File:** x/uexecutor/keeper/handler.go (L12-46)
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
}
```

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
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
