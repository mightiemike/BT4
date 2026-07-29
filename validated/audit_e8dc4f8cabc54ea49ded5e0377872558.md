## Analog Found: Missing Liquidity-Cap Enforcement on PRC20 Minting (`x/uexecutor` / `x/uregistry`)

### Title
Unbounded PRC20 minting — `TokenConfig.LiquidityCap` is declared and validated but never enforced at mint time - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The TraitForge bug is a class of "declared maximum that is never checked at the mutation site" — the contract models a `maxGeneration` cap but the generation-increment function never compares against it. Push Chain's `uregistry` module has the same shape of bug: `TokenConfig.LiquidityCap` is documented as "max supply cap for this token" [1](#0-0)  and is a mandatory, validated field (`ValidateBasic` rejects an empty `LiquidityCap`) [2](#0-1) , but the field is never read anywhere in the actual PRC20 minting path.

### Finding Description
Every inbound deposit that carries funds (`FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`, CEA variants) eventually calls `depositPRC20`, which looks up the `TokenConfig` only to resolve the PRC20 contract address and then mints unconditionally: [3](#0-2) 

`depositPRC20` calls `CallPRC20Deposit`, which issues a `DerivedEVMCall` to `depositPRC20Token` on the `UNIVERSAL_CORE` handler contract with the raw inbound `amount`, with no reference to `tokenConfig.LiquidityCap` anywhere in the call chain: [4](#0-3) 

A repo-wide search for `LiquidityCap` shows zero occurrences inside `x/uexecutor/**` — the field only appears in `uregistry` proto/types/validation code and in test fixtures that populate it as a required parameter, never in any keeper logic that gates a mint. This mirrors the TraitForge pattern exactly: the cap exists as declared state (`maxGeneration` there, `LiquidityCap` here) and is validated at config-write time, but the code path that actually performs the bounded action (`_incrementGeneration` minting NFTs / `depositPRC20` minting PRC20) never consults it.

### Impact Explanation
Any unprivileged external-chain user can repeatedly submit ordinary cross-chain deposits (`Inbound` observations with `TxType_FUNDS`/`FUNDS_AND_PAYLOAD`/etc.) for a registered token. Once honest Universal Validators observe and vote these inbounds through the normal ballot-finalization flow (no malicious validator assumption required), `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload` deposit the full requested amount into the recipient's PRC20 balance with no check against `TokenConfig.LiquidityCap`. This directly corrupts PRC20 accounting: the synthetic PRC20 supply on Push Chain can grow past the value that governance/admin configured as the safety ceiling for that asset, an "unauthorized mint" of protocol-controlled synthetic value and a "corruption of PRC20 ... accounting" as defined in the allowed-impact gate — reachable purely through default, honest-validator-approved user deposits.

### Likelihood Explanation
High reachability: the trigger is the standard, everyday inbound-deposit flow (`MsgVoteInbound` → ballot finalize → `ExecuteInboundFunds*`) that every bridging user already exercises; no special privileges, malicious peers, or protocol misconfiguration are needed beyond a token being registered with any `LiquidityCap` value (which is mandatory for every token). The only actions required are (a) locking/sending funds on the source chain and (b) waiting for the existing honest-validator quorum to vote the inbound — both are part of intended normal usage.

### Recommendation
Track cumulative minted/circulating PRC20 supply per `(chain, token)` (or query the PRC20 contract's `totalSupply`) inside `depositPRC20` before issuing `CallPRC20Deposit`/`CallPRC20DepositAutoSwap`, and reject or clamp deposits that would push supply past `tokenConfig.LiquidityCap`, analogous to adding the `currentGeneration <= maxGeneration` check the TraitForge report recommends. Emit the failure into the existing `PCTx` FAILED path (as already done for other deposit failures) so it can trigger the existing revert-outbound refund flow instead of silently minting past the cap.

### Proof of Concept
1. Register a `TokenConfig` for `eip155:11155111` / USDC with `LiquidityCap = "1000000000000000000000000"` (as done in the existing test fixtures, e.g. `x/uregistry` integration tests) [5](#0-4) .
2. As an ordinary external-chain user, submit repeated `Inbound` observations of type `FUNDS` for that token/chain pair with `Amount` values that, summed, exceed `LiquidityCap` (e.g., mint `2,000,000` PRC20 units against a `1,000,000`-unit cap).
3. Have the existing honest validator set vote each inbound to quorum via `MsgVoteInbound`, exactly as in `utils.ExecVoteInbound` used throughout the test suite [6](#0-5) .
4. Observe that `ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` succeeds for every deposit regardless of cumulative minted amount, since no code path reads `tokenConfig.LiquidityCap` [3](#0-2) , confirming PRC20 supply exceeds the configured cap.

### Citations

**File:** proto/uregistry/v1/types.proto (L141-141)
```text
  string liquidity_cap = 7;                // max supply cap for this token (string big.Int format)
```

**File:** x/uregistry/types/token_config.go (L56-58)
```go
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

**File:** test/integration/uexecutor/inbound_zero_amount_test.go (L52-65)
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
```

**File:** test/integration/uexecutor/inbound_zero_amount_test.go (L85-98)
```go
	for i, val := range validators {
		accAddr, err := sdk.ValAddressFromBech32(val.OperatorAddress)
		require.NoError(t, err)

		coreValAddr := sdk.AccAddress(accAddr)
		uniValAddr := sdk.MustAccAddressFromBech32(universalVals[i])

		msgType := sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{})
		auth := authz.NewGenericAuthorization(msgType)
		exp := ctx.BlockTime().Add(time.Hour)

		err = chainApp.AuthzKeeper.SaveGrant(ctx, uniValAddr, coreValAddr, auth, &exp)
		require.NoError(t, err)
	}
```
