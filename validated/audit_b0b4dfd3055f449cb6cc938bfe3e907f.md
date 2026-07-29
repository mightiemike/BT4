No `.sol` file in the repo references `liquidityCap` at all — confirming the cap is not enforced anywhere, neither in the Go keeper nor in the on-chain PRC20/UniversalCore contracts that the repo bundles.

### Title
Unenforced per-token `liquidity_cap` allows unbounded PRC20 minting via ordinary inbound deposits - (File: `x/uregistry/types/token_config.go`, `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`TokenConfig.LiquidityCap` is defined and required to be a non-empty value at registration time, but no code path in the inbound-deposit / PRC20-mint pipeline ever reads or checks it. Any inbound deposit voted through by honest Universal Validators is minted in full regardless of how large the accumulated PRC20 supply for that token has become, directly analogous to the reported `maxAmounts` issue where a configured limit is never actually summed/enforced.

### Finding Description
`TokenConfig` carries a `liquidity_cap` field described as the "max supply cap for this token" [1](#0-0) , and `ValidateBasic` only checks that the string is non-empty — it never parses it as a bound to be compared against anything [2](#0-1) .

The actual minting path — `depositPRC20` (looked up via `uregistryKeeper.GetTokenConfig`, then dispatches to the PRC20 handler contract) — never fetches or checks `LiquidityCap`; it only checks that `NativeRepresentation` exists and parses the amount string [3](#0-2) . The EVM call that actually performs the mint, `CallPRC20Deposit`, likewise contains no cap check — it just derives an EVM call to `depositPRC20Token` on the `UNIVERSAL_CORE` handler contract with the raw amount [4](#0-3) . A repo-wide search for `LiquidityCap`/`liquidity_cap` shows the getter (`GetLiquidityCap()`) is emitted only in generated protobuf/pulsar code and is never called from any keeper logic, and no `.sol` contract in the repo references `liquidityCap` at all, confirming there is no on-chain enforcement path either.

This same unbounded-mint pattern exists across all three inbound execution flows that call `depositPRC20`/the autoswap variant: `ExecuteInboundFunds` [5](#0-4) , `ExecuteInboundFundsAndPayload` [6](#0-5) , and `ExecuteInboundGasAndPayload` [7](#0-6) . None of them compare the running total PRC20 supply (or the sum of deposits) against `TokenConfig.LiquidityCap` before minting.

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting" / "unauthorized mint" in the allowed impact gate. `liquidity_cap` is meant to bound the total PRC20 exposure that Push Chain is willing to back for a given external asset (analogous to `amountsMax`/`totalSupply` in the report). Because it is never enforced, an ordinary unprivileged user can repeatedly deposit the same external asset (or a single very large deposit) on the source chain and have honest, non-malicious validators vote the inbound through — as demonstrated by the "multiple inbounds accumulate balances" test which shows unrestricted, unbounded PRC20 accumulation for a recipient with no cap check anywhere in the flow [8](#0-7) . This can silently blow past the configured risk/collateral cap for that token, corrupting the intended token-accounting invariant even though vault/backing collateral on the source chain never actually reaches that level, or the operational assumption that no more than `liquidity_cap` worth of that token will ever be represented on Push Chain.

### Likelihood Explanation
High. No privileged action or malicious validator is required — this is triggered purely by unprivileged, ordinary user deposit activity through the standard, honest-validator inbound-voting path, exactly matching the "reachable from ordinary user deposits ... default transaction submission paths alone" scope. There is no special crafted payload needed; simply making many deposits (or one large one) exceeds any intended cap since nothing checks it.

### Recommendation
Add an internal keeper function that, before completing a PRC20 deposit/mint, computes the token's total minted/circulating supply (e.g., via `totalSupply()` on the PRC20 contract or an internally tracked running total) plus the incoming amount, and reverts (marks the inbound execution as failed, consistent with existing `execErr`/`shouldRevert` patterns) if this exceeds `TokenConfig.LiquidityCap`. Call this check from `depositPRC20` (`x/uexecutor/keeper/handler.go`) and from the gas/payload autoswap-deposit path, before invoking `CallPRC20Deposit`/`gasAndPayloadDepositAutoSwap`, so both funds-only and funds-and-payload/gas-and-payload flows are covered.

### Proof of Concept
1. Register a `TokenConfig` for an external asset with `liquidity_cap = "1000000"` (any bound).
2. As an ordinary external-chain user, submit deposits to the gateway on the source chain whose cumulative `Amount` across several inbound events exceeds `1000000`.
3. Honest Universal Validators observe and vote each inbound normally (`MsgVoteInbound`); quorum is reached for each, exactly as exercised in `TestInboundSyntheticBridge`'s "multiple inbounds accumulate balances" sub-test [8](#0-7) .
4. `ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` mints the full amount for every inbound with no reference to `LiquidityCap`, so the recipient's PRC20 balance (and total token supply) grows past the configured cap with no error or revert at any point.

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L68-100)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L76-97)
```go
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

**File:** test/integration/uexecutor/inbound_synthetic_bridge_test.go (L290-327)
```go
	t.Run("multiple inbounds accumulate balances", func(t *testing.T) {
		app, ctx, vals, inbound, coreVals := setupInboundBridgeTest(t, 4)
		ueModuleAccAddress, _ := app.UexecutorKeeper.GetUeModuleAddress(ctx)
		recipient := common.HexToAddress(inbound.Recipient)

		// First inbound
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, app, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}

		// Second inbound with different TxHash
		inboundB := *inbound
		inboundB.TxHash = "0xabcf"
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, app, vals[i], coreValAcc, &inboundB)
			require.NoError(t, err)
		}

		// balance should equal 2 * inbound.Amount
		res, err := app.EVMKeeper.CallEVM(ctx, prc20ABI, ueModuleAccAddress, prc20Address, false, nil, "balanceOf", recipient)
		require.NoError(t, err)
		balances, _ := prc20ABI.Unpack("balanceOf", res.Ret)

		expected := new(big.Int)
		expected.SetString(inbound.Amount, 10)
		expected.Mul(expected, big.NewInt(2))

		require.Equal(t, 0, balances[0].(*big.Int).Cmp(expected))
	})
```
