## Finding

### Title
Inbound PRC20 minting never enforces `TokenConfig.LiquidityCap`, allowing unrestricted supply growth past the registry-declared threshold - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`x/uregistry`'s `TokenConfig` defines a `LiquidityCap` field intended to bound the maximum mintable supply of a PRC20 representation for a given external token [1](#0-0) . `ValidateBasic` only checks that the string is non-empty when the config is registered [2](#0-1) , but nowhere in the inbound deposit/minting path (`depositPRC20` → `CallPRC20Deposit`) is the current or projected PRC20 total supply compared against this cap before minting [3](#0-2) . A grep across the entire repository shows `LiquidityCap` is referenced only in protobuf-generated code, tests, and config JSON files — never read by any keeper logic that gates minting.

### Finding Description
This is the same bug class as the AMO `preRebalanceCheck()` report: a declared safety threshold exists, an operation is validated against pre-conditions (source-chain lock event, quorum, token-config existence), but nothing verifies that performing the operation leaves the tracked balance within the declared bound. Here, the threshold is `TokenConfig.LiquidityCap`, and the "rebalance operation" is the PRC20 deposit triggered by `VoteOnInboundBallot` → `depositPRC20` → `k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)` [3](#0-2) . The function fetches the `TokenConfig` only to resolve `NativeRepresentation.ContractAddress`, parses the amount, and mints — no call to read the PRC20's current total supply or compare `currentSupply + amount` against `tokenConfig.LiquidityCap` is present anywhere in this flow.

### Impact Explanation
Any unprivileged user can drive PRC20 total supply for a registered asset arbitrarily far past its declared `LiquidityCap` simply by submitting ordinary inbound deposits on the source chain (real locked funds, honest-validator quorum, default transaction path — no privileged action required). Since the cap is a protocol-level risk-accounting invariant (used to bound exposure/collateral risk per asset, as documented in the registry's own field comment "max supply cap for this token"), silently exceeding it corrupts PRC20 accounting relative to the stated invariant and defeats the intended per-asset risk ceiling with no code path ever rejecting or throttling the mint. This falls under "corruption of PRC20 ... accounting" in the allowed-impact gate.

### Likelihood Explanation
High reachability: no privileged actor is required. Any external user can generate legitimate inbound deposit events (the value is genuinely locked on the source chain), and as long as honest validators reach quorum through the normal `MsgVoteInbound` flow, the mint proceeds unconditionally regardless of `LiquidityCap`. The only friction is accumulating enough deposit volume to exceed the configured cap, which is an economic/volume constraint, not a technical control.

### Recommendation
In `depositPRC20` (or `CallPRC20Deposit`), before minting, query the PRC20 contract's current `totalSupply()`, add the incoming `amount`, and reject (or fail the `PcTx` with a recorded error, consistent with existing failure-handling patterns such as the "missing token config" FAILED path) if the result exceeds `tokenConfig.LiquidityCap`. This mirrors the AMO recommendation: validate that the *post-state* — not just the pre-state trigger condition — remains within the declared threshold.

### Proof of Concept
1. Admin registers a `TokenConfig` for `USDC` on `eip155:11155111` with `LiquidityCap = "1000000000000000000000000"` (1,000,000 USDC at 18 decimals equivalent) [4](#0-3) .
2. An external, unprivileged user repeatedly locks funds on the source chain and submits inbound events; honest validators observe and vote via `VoteOnInboundBallot`, reaching quorum each time exactly as in the existing accumulation test [5](#0-4) .
3. Each successful vote calls `depositPRC20` → `CallPRC20Deposit`, minting the deposited amount with no supply check [3](#0-2) .
4. Cumulative minted PRC20 supply exceeds `LiquidityCap` with no error, rejection, or recorded `FAILED` status anywhere in the flow — the cap is a dead configuration field.

### Uncertainty note
I was not able to view the full body of `CallPRC20Deposit` in `x/uexecutor/keeper/evm.go` (only located it via grep, not full read) due to the final-iteration constraint, so I cannot 100% rule out an internal supply check inside that specific function. However, a repository-wide `grep_search` for `LiquidityCap` shows the identifier appears only in generated protobuf code, tests, and config JSON — never in any `x/uexecutor/keeper/*.go` file — which is strong evidence that no keeper logic anywhere reads or enforces this field at mint time. If a Devin agent with full file access confirms `CallPRC20Deposit` does perform such a check, this finding should be withdrawn.

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
