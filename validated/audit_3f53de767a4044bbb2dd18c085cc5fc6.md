### Title
Configured token `LiquidityCap` is never enforced during PRC20 minting, allowing unbounded PRC20 supply to be minted past the registry's supply-cap invariant - (File: `x/uexecutor/keeper/handler.go`)

### Summary
`uregistry.TokenConfig.LiquidityCap` is documented as the "max supply cap for this token" [1](#0-0)  and is required to be non-empty at config-creation time [2](#0-1) . However, nothing in the reachable inbound-deposit/minting path (`x/uexecutor`) ever reads or checks `LiquidityCap` against the amount being minted, so the cap is purely cosmetic — the same defect class as EIP-4626's `maxMint`/`maxDeposit` not reflecting `maxSupply`.

### Finding Description
When an inbound deposit is processed and quorum is reached, `ExecuteInboundFundsAndPayload` (and the plain-funds equivalent) calls `k.depositPRC20(...)` with the raw inbound amount [3](#0-2) . `depositPRC20` looks up the `TokenConfig` only to resolve the PRC20 contract address, and never inspects `LiquidityCap`: [4](#0-3) 

The amount is then forwarded unconditionally to the PRC20 contract's `depositPRC20Token` method via `CallPRC20Deposit`, again with no cap check in the Go keeper code: [5](#0-4) 

A `grep` across the entire `x/uexecutor` module confirms `LiquidityCap` is referenced nowhere outside of test fixtures and the `uregistry` config-validation code — it is validated as "must be non-empty" at admin-config time only, never compared against cumulative minted supply during execution. I was unable to inspect the PRC20 Solidity contract itself (it is not present in this repository's index — the contracts likely live in a separate `core-contracts` repository), so I cannot confirm whether the cap is enforced on-chain at the EVM contract layer instead. If it is not enforced there either, then no component in the accessible codebase upholds the `LiquidityCap` invariant at all.

### Impact Explanation
If `LiquidityCap` is intended to bound total PRC20 supply per source-chain token (e.g., to cap the protocol's exposure/backing-liability per asset, matching vault reserve capacity, or bounding blast radius of a compromised source-chain bridge event), an unprivileged actor can drive cumulative PRC20 minting for a token past its configured cap simply by making (or having honest validators observe) enough genuine inbound deposits — nothing in the finalize/execute path stops it. This corrupts the PRC20 accounting invariant that total minted supply for a token tracks its registry-configured cap, which is the direct analog of the ERC-4626 `maxMint`/`maxDeposit` non-compliance in the source report (both fail to bound issuance by a configured supply ceiling).

### Likelihood Explanation
Medium-to-low: the mechanism is reachable through ordinary, honest-validator-approved inbound deposit flow, requiring no privileged access — only enough legitimate external-chain deposit volume (or repeated deposits) to exceed the configured cap. It does not require malicious validators, since finalization here is per the honest-quorum voting model already in scope.

### Recommendation
Enforce `LiquidityCap` in `depositPRC20` (or `CallPRC20Deposit`) by reading the token's current total PRC20 supply (e.g., via a `totalSupply()` EVM call) and rejecting/capping the deposit if `currentSupply + amount > LiquidityCap`, mirroring the WATCHPUG-recommended `maxMint`/`maxDeposit` pattern that clamps to `maxSupply - totalSupply`. This check should live in the same place `NativeRepresentation` is resolved in `x/uexecutor/keeper/handler.go`.

### Proof of Concept
Not independently verifiable end-to-end from this repository alone (the PRC20 Solidity contract that would receive `depositPRC20Token` calls is not indexed here), but the Go-side control-flow trace is directly reproducible:
1. Register a `TokenConfig` with a small `LiquidityCap`, e.g., `"1000000"` [6](#0-5) .
2. Submit repeated `Inbound` FUNDS votes (as in `TestSolanaInboundFunds`'s "multiple solana FUNDS inbounds accumulate balance" case) whose cumulative `Amount` exceeds `LiquidityCap` [7](#0-6) .
3. Observe that `depositPRC20`/`CallPRC20Deposit` mint the full amount each time with no cap check, since `LiquidityCap` is never read in that code path [4](#0-3) .

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L144-157)
```go
			if execErr == nil && inboundAmount.Sign() > 0 {
				receipt, err = k.depositPRC20(
					sdkCtx,
					utx.InboundTx.SourceChain,
					utx.InboundTx.AssetAddr,
					ueaAddr,
					utx.InboundTx.Amount,
				)
				if err != nil {
					execErr = fmt.Errorf("depositPRC20 failed: %w", err)
					shouldRevert = true
					revertReason = execErr.Error()
				}
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

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L54-67)
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

**File:** test/integration/uexecutor/inbound_solana_test.go (L167-189)
```go
	t.Run("multiple solana FUNDS inbounds accumulate balance", func(t *testing.T) {
		app, ctx, vals, inbound, coreVals := setupSolanaInboundTest(t, 4, uexecutortypes.TxType_FUNDS)

		ueModuleAccAddress, _ := app.UexecutorKeeper.GetUeModuleAddress(ctx)
		recipient := common.HexToAddress(inbound.Recipient)

		// First inbound
		voteToQuorum(t, ctx, app, vals, coreVals, inbound)

		// Second inbound with different tx hash
		inbound2 := *inbound
		inbound2.TxHash = "3kHu2qwD7q5xMkZxq6z2S3r4y5N7m8P9kL0jH1gF2dE"
		voteToQuorum(t, ctx, app, vals, coreVals, &inbound2)

		// Balance should be 2x
		res, err := app.EVMKeeper.CallEVM(ctx, prc20ABI, ueModuleAccAddress, prc20Address, false, nil, "balanceOf", recipient)
		require.NoError(t, err)
		balances, _ := prc20ABI.Unpack("balanceOf", res.Ret)
		expected := new(big.Int)
		expected.SetString(inbound.Amount, 10)
		expected.Mul(expected, big.NewInt(2))
		require.Equal(t, 0, balances[0].(*big.Int).Cmp(expected))
	})
```
