## Finding

This confirms the analog: `LiquidityCap` is stored on `TokenConfig` and its presence is validated as a non-empty string in `ValidateBasic()`, but there is no keeper code anywhere in `x/uexecutor` (the module that actually performs `depositPRC20` / `CallPRC20Deposit` mints on inbound execution) that reads `LiquidityCap` and compares it against current minted/circulating supply before minting. This is the same class of bug as the Rubicon `enforceReserveRatio` finding: a security parameter is declared as the mechanism that should bound value movement, but the code path that actually moves value (mint via `depositPRC20`) never consults it.

### Title
Declared `TokenConfig.LiquidityCap` Is Never Enforced During PRC20 Minting - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`x/uregistry` stores a `liquidity_cap` field on every `TokenConfig`, documented as the "max supply cap for this token" [1](#0-0) . `MsgAddTokenConfig`/`MsgUpdateTokenConfig` only require the field to be a non-empty string via `ValidateBasic()` [2](#0-1) , but no reachable code in `x/uexecutor` reads this value before minting PRC20 tokens to a recipient during inbound execution.

### Finding Description
Every inbound of type `FUNDS`, `FUNDS_AND_PAYLOAD`, or `GAS_AND_PAYLOAD` that carries a positive `Amount` triggers `depositPRC20`, which looks up the `TokenConfig` purely to resolve the PRC20 contract address and then calls `CallPRC20Deposit` to mint the requested amount unconditionally: [3](#0-2) 

`CallPRC20Deposit` performs the mint via `DerivedEVMCall` to `UniversalCore.depositPRC20Token(prc20Address, amount, to)` with no supply-cap check anywhere in the call chain: [4](#0-3) 

The `TokenConfig.LiquidityCap` field is only ever validated for non-emptiness at admin-registration time [5](#0-4) ; `GetTokenConfig` is used purely to fetch `NativeRepresentation.ContractAddress` in the execution path [6](#0-5) , and no comparison against total minted PRC20 supply is performed before or after the deposit call. This mirrors the Rubicon `enforceReserveRatio` pattern precisely: a declared risk-bounding parameter (`ReserveRatio` / `LiquidityCap`) exists in state and is checked for shape validity at configuration time, but the actual value-moving function (`placeMarketMakingTrades` / `depositPRC20`) never consults it to cap the amount that can flow through.

### Impact Explanation
Because `Inbound.ValidateForExecution()` only checks that `Amount` parses as a non-negative `uint256` [7](#0-6) , and honest Universal Validators vote based on an observed external-chain event whose `Amount` field is attacker-controlled data emitted by the attacker's own transaction on the source chain (e.g. a spoofed or inflated Gateway event payload, or genuinely large deposits), a user can mint PRC20 representations of an external asset on Push Chain in amounts unconstrained by the registry's declared `liquidity_cap`. If `LiquidityCap` was intended to bound the synthetic-asset supply as a risk control (analogous to how Rubicon's ReserveRatio was meant to bound strategist utilization), its absence from the mint path means:
- The PRC20 supply for a token can exceed the amount of real underlying collateral custodied on the source chain / vault, corrupting PRC20 accounting relative to the token's registered risk cap.
- This is an unauthorized/unbounded mint of protocol-controlled synthetic assets relative to the declared invariant, falling under "unauthorized mint" and "corruption of PRC20 accounting" in the allowed-impact gate.

### Likelihood Explanation
This is reachable via the default, unprivileged inbound flow: any user can generate a source-chain event through the Gateway and have honest Universal Validators vote it in through `MsgVoteInbound`; no admin or validator misbehavior is required. The only gating condition is that the underlying source-chain deposit event exists and reaches quorum — an attacker fully controls the timing and volume of their own deposits, and each individual deposit is validated only for basic type-correctness, never against `liquidity_cap`.

### Recommendation
Enforce `TokenConfig.LiquidityCap` inside `depositPRC20` (or `CallPRC20Deposit`) by tracking/reading the current total minted supply for the PRC20 (e.g., via `totalSupply()` on the PRC20 contract or an on-chain supply counter in `x/uregistry`/`x/uexecutor`) and rejecting or reverting the mint (producing a `FAILED` `PCTx` and, where applicable, an `INBOUND_REVERT` outbound) when `currentSupply + amount > LiquidityCap`.

### Proof of Concept
Note: I could not fully verify at what layer (Solidity `PRC20`/`UniversalCore` contracts, out of this repo's scope) any supply cap enforcement might exist, since those contracts are not part of the indexed Go codebase and their bytecode/ABI details are not fully retrievable here. The claim is limited to: no Go-level keeper code in `x/uexecutor` or `x/uregistry` reads or enforces `LiquidityCap` before or after minting. If the `UniversalCore.depositPRC20Token` EVM contract itself enforces a cap, this finding would be moot — but nothing in the scoped Go repository proves that enforcement exists, and the field's only consumer found across the entire codebase is `ValidateBasic()`. A defender should confirm whether `depositPRC20Token` (Solidity, referenced but not included in this repo) checks liquidity cap; if not, a PoC would be: register a `TokenConfig` with `liquidity_cap = 100`, then submit/observe an inbound with `Amount = 100000000` for that same token, reach UV quorum via `MsgVoteInbound`, and observe the PRC20 balance/mint succeed for the full amount as shown in the test harness pattern at [8](#0-7) .

### Citations

**File:** proto/uregistry/v1/types.proto (L141-141)
```text
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
```

**File:** x/uregistry/types/token_config.go (L22-58)
```go
// ValidateBasic performs sanity checks on the TokenConfig
func (p TokenConfig) ValidateBasic() error {
	if strings.TrimSpace(p.Chain) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "chain cannot be empty")
	}

	if strings.TrimSpace(p.Address) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token contract address cannot be empty")
	}

	// Enforce a parseable address for the chain's namespace (e.g. 20-byte hex
	// for eip155, base58 for solana) so every registration lands on the
	// canonical storage key.
	if _, err := utils.CanonicalizeAddressByNamespace(p.Chain, p.Address); err != nil {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token address for chain %s: %s", p.Chain, err)
	}

	if strings.TrimSpace(p.Name) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token name cannot be empty")
	}

	if strings.TrimSpace(p.Symbol) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token symbol cannot be empty")
	}

	if p.Decimals == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "decimals must be greater than zero")
	}

	// Validate token_type is within known enum range
	if _, ok := TokenType_name[int32(p.TokenType)]; !ok {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token_type: %v", p.TokenType)
	}

	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
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

**File:** x/uexecutor/types/inbound.go (L126-138)
```go
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}
```

**File:** test/integration/uexecutor/inbound_cea_gas_and_payload_test.go (L54-70)
```go
	tokenConfigTest := uregistrytypes.TokenConfig{
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

	chainApp.UregistryKeeper.AddChainConfig(ctx, &chainConfigTest)
	chainApp.UregistryKeeper.AddTokenConfig(ctx, &tokenConfigTest)
```
