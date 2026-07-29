### Title
Missing `LiquidityCap` enforcement lets unbounded PRC20 minting bypass the per-token exposure limit - (File: `x/uexecutor/keeper/handler.go`)

### Summary
`uregistry.TokenConfig.LiquidityCap` is documented and validated as "max supply cap for this token" and is a mandatory, non-empty field on every registered token [1](#0-0) [2](#0-1) . However, the actual PRC20-minting code path (`depositPRC20` → `CallPRC20Deposit`) never reads or checks this value against cumulative minted supply before minting [3](#0-2) [4](#0-3) . This mirrors the external report's core theme: a numeric safety bound intended to rate-limit/cap exposure exists on paper but is not enforced at the value-creation choke point, letting attacker-controlled inbound volume escalate mint exposure unchecked, exactly as the LST report describes unchecked share-increase via deposit with no rate limiting.

### Finding Description
Every whitelisted token config carries a `LiquidityCap` intended to bound how much PRC20 supply Push Chain will mint against that source-chain asset [5](#0-4) . The inbound execution flow (`ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`) calls `k.depositPRC20(...)` for every successfully quorum-finalized inbound, which fetches the `TokenConfig`, resolves the PRC20 contract address, and calls `CallPRC20Deposit` to mint the requested `amount` with no reference to `LiquidityCap` anywhere in the call chain [6](#0-5) [7](#0-6) . Neither `depositPRC20` nor `CallPRC20Deposit` nor any pre-execution validation step (`ValidateForExecution`, `VoteInbound`) tracks or checks cumulative minted supply against the configured cap.

Because inbound minting is driven purely by honest Universal Validators observing and voting on real, user-triggered on-chain events on the source chain (an ordinary unprivileged flow, not requiring any validator or admin misbehavior), a user can repeatedly bridge the same token in successive inbound transactions and Push Chain will keep minting PRC20 without ever consulting the configured cap — even though the field is mandatory at registration time and clearly intended as a safety rail against exactly this kind of unbounded exposure growth.

### Impact Explanation
This corrupts PRC20/native asset accounting: the protocol's per-asset exposure limit — the very knob meant to protect the protocol and let operators respond to abnormal bridging activity for a given token (analogous to the LST report's rate-limit recommendation) — is silently dead code. An attacker (or just organic heavy usage) can inflate a token's PRC20 supply on Push Chain far past its configured `LiquidityCap`, defeating the risk-isolation the registry was designed to provide and creating unbacked/over-minted PRC20 balances relative to the intended safety ceiling for that asset.

### Likelihood Explanation
High. No privileged action or external-chain compromise is required — an ordinary user submitting legitimate, correctly-observed inbound bridge transactions in volume is sufficient to breach the cap, since the enforcement code simply does not exist anywhere in the mint path.

### Recommendation
Enforce `TokenConfig.LiquidityCap` at the point of minting: track cumulative minted PRC20 supply per `(chain, token)` (or query current PRC20 `totalSupply` via `CallEVM`) inside `depositPRC20`/`CallPRC20Deposit`, and reject/fail the deposit (recording a `FAILED` PCTx and triggering the existing revert-outbound path) when the post-mint total would exceed `LiquidityCap`, mirroring the rate-limit pattern in the external report.

### Proof of Concept
1. Admin registers a `TokenConfig` for `eip155:1` USDC with `LiquidityCap = "1000000000000000000000000"` [8](#0-7) .
2. A user repeatedly deposits real USDC into the gateway on the source chain across many inbound transactions (each a legitimate, honestly-observed event).
3. Each inbound reaches UV quorum via `VoteInbound` and is executed via `ExecuteInboundFunds`, which calls `depositPRC20` → `CallPRC20Deposit` to mint the exact requested amount every time, with no check against the sum minted so far or `LiquidityCap` [6](#0-5) .
4. Cumulative minted PRC20 supply for that token exceeds the configured `LiquidityCap` with no error, no failed PCTx, and no defensive pause — the cap has no operational effect (confirmed by test `TestSolanaInboundFunds/multiple solana FUNDS inbounds accumulate balance` showing repeated minting with no cap check) [9](#0-8) .

### Citations

**File:** api/uregistry/v1/types.pulsar.go (L5721-5721)
```go
	LiquidityCap         string                `protobuf:"bytes,7,opt,name=liquidity_cap,json=liquidityCap,proto3" json:"liquidity_cap,omitempty"`                         // max supply cap for this token (string big.Int format)
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

**File:** x/uregistry/README.md (L7-7)
```markdown
- **Stores chain configs** — for each supported external chain (CAIP-2 keyed): public RPC URL, gateway contract address, gateway/vault method identifiers, block confirmation thresholds, gas oracle fetch interval, VM type, and inbound/outbound enabled flags.
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
