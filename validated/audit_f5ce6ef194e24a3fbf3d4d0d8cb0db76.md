### Title
Configured per-token `LiquidityCap` is never enforced during PRC20 minting, allowing unbounded issuance of any bridged asset - (File: x/uexecutor/keeper/handler.go)

### Summary
`x/uregistry`'s `TokenConfig.LiquidityCap` is documented and validated as "max supply cap for this token" and is a required, non-empty field on every registered token [1](#0-0) , enforced only for non-emptiness at registration time [2](#0-1) . However, the actual PRC20 minting path — `depositPRC20` → `CallPRC20Deposit` → `DerivedEVMCall("depositPRC20Token", ...)` — never reads or checks `LiquidityCap` anywhere before minting [3](#0-2) [4](#0-3) . This mirrors the external report's core defect (a supply-limiting control that exists on paper but is never actually enforced at issuance time), giving an analogous "supply-cap bypass" class of bug in Push Chain's PRC20 minting instead of the USD token's Fed-controlled minting.

### Finding Description
Every inbound `FUNDS`, `FUNDS_AND_PAYLOAD`, and `GAS`/`GAS_AND_PAYLOAD` (auto-swap deposit) execution path mints PRC20 tokens equal to `inbound.Amount` by calling `depositPRC20`:

```go
func (k Keeper) depositPRC20(
	ctx sdk.Context, sourceChain string, assetAddr string,
	recipient common.Address, amountStr string,
) (*vmtypes.MsgEthereumTxResponse, error) {
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
	...
	amount, ok := amount.SetString(amountStr, 10)
	...
	return k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)
}
``` [3](#0-2) 

`tokenConfig` is fetched (which contains `LiquidityCap`), but the field is read only to reach `NativeRepresentation` — the `LiquidityCap` value is never compared against the requested `amount` or against the token's cumulative minted supply anywhere in this function, in `CallPRC20Deposit` [4](#0-3) , in `CallPRC20DepositAutoSwap` [5](#0-4) , or in `ExecuteInboundFunds` / `ExecuteInboundFundsAndPayload` / `ExecuteInboundGas` which drive these calls [6](#0-5) [7](#0-6) [8](#0-7) . A repo-wide search for `LiquidityCap` usage confirms the field only ever appears in proto/generated types, validation tests, and test fixtures — never in any keeper logic that gates a mint.

This means the only thing standing between an inbound event and PRC20 minting is UV quorum on the observed source-chain amount — there is no independent, on-chain circuit breaker that caps how much of a given PRC20 can ever be minted, regardless of what admins configured in `TokenConfig.LiquidityCap`. Since inbound execution is triggered purely by ordinary user-initiated source-chain deposits (any user can call the real gateway/vault contract with an arbitrary, self-chosen amount up to whatever the source-chain contract allows) and honest UVs simply relay the observed amount via `MsgVoteInbound` [9](#0-8) , an unprivileged attacker fully controls the minted amount subject only to what the (out-of-scope) external gateway allows — the admin-configured per-token risk ceiling that operators rely on as a defense-in-depth control provides no actual protection on Push Chain.

### Impact Explanation
`LiquidityCap` is presented in the registry schema as the safety valve against over-minting a given synthetic/PRC20 asset (e.g., to bound blast radius from a compromised or misconfigured source-chain contract, a bridge bug, or a runaway relay). Because it is silently unenforced, that control provides no actual protection: PRC20 supply for any registered token can grow without any protocol-level ceiling, undermining accounting invariants that downstream systems (liquidity pools, swap quoting, gas-refund auto-swap paths) implicitly assume are bounded. This falls within "corruption of PRC20 ... accounting" and "unauthorized mint ... of user or protocol-controlled funds" under the impact gate, since the configured invariant meant to bound mintable supply is bypassed by ordinary, honest-validator-attested user deposits.

### Likelihood Explanation
High reachability, but impact is conditional on the operational assumption that `LiquidityCap` is actually load-bearing rather than purely advisory/off-chain (e.g., enforced by monitoring or by the external vault's own lock capacity). No unprivileged authentication bypass or validator misbehavior is required — only ordinary deposits through the real, correctly configured gateway on any enabled source chain, observed and voted by honest UVs exactly as designed. The severity is bounded by whatever value an attacker can actually lock/spend on the source chain (this is a lock-and-mint bridge, so minting still requires a corresponding source-chain transfer); the missing check does not by itself create unbacked supply, but it removes the intended per-token ceiling that is supposed to bound exposure to a single asset, config error, or high-value/permissionless token listing.

### Recommendation
Enforce `TokenConfig.LiquidityCap` on the mint path: before calling `CallPRC20Deposit`/`CallPRC20DepositAutoSwap`, track (or read from the PRC20 contract) the token's current minted supply and reject/fail the deposit (recording a `FAILED` PCTx and triggering the standard revert path) when `currentSupply + amount > LiquidityCap`. This should be added in `depositPRC20` (`x/uexecutor/keeper/handler.go`) and mirrored in the auto-swap deposit path used by `ExecuteInboundGas`.

### Proof of Concept
1. Register a `TokenConfig` for chain `eip155:X` / asset `A` with `LiquidityCap = "1000000"` (as done in every test fixture, e.g. `test/integration/uexecutor/inbound_synthetic_bridge_test.go`).
2. As an ordinary user, deposit/lock any amount `> 1000000` of the underlying asset on the real source-chain gateway (or repeat smaller deposits whose cumulative total exceeds the cap).
3. Honest UVs observe and vote the real event(s) via `MsgVoteInbound`; quorum is reached exactly as in `TestSolanaInboundFunds`'s "multiple ... inbounds accumulate balance" case [10](#0-9) .
4. `ExecuteInboundFunds` → `depositPRC20` → `CallPRC20Deposit` mints the full requested amount with no reference to `tokenConfig.LiquidityCap` anywhere in the call chain [3](#0-2) .
5. Query `balanceOf`/total minted supply for the PRC20 and observe it exceeds the configured `LiquidityCap`, confirming the cap is decorative only.

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

**File:** x/uexecutor/keeper/evm.go (L542-593)
```go
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
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
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L11-30)
```go
func (k Keeper) ExecuteInboundFunds(ctx context.Context, utx types.UniversalTx) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	inbound := utx.InboundTx

	k.Logger().Info("execute inbound funds: depositing PRC20",
		"utx_key", utx.Id,
		"source_chain", inbound.SourceChain,
		"recipient", inbound.Recipient,
		"amount", inbound.Amount,
		"is_cea", inbound.IsCEA,
	)

	receipt, err := k.depositPRC20(
		sdkCtx,
		inbound.SourceChain,
		inbound.AssetAddr,
		common.HexToAddress(inbound.Recipient), // recipient is inbound recipient
		inbound.Amount,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-102)
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

			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
			} else if isUEA {
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
			}
		}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-148)
```go
	// --- step 1: get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
		// --- step 2: parse amount
		amount := new(big.Int)
		if amount, ok := amount.SetString(inbound.Amount, 10); !ok {
			execErr = fmt.Errorf("invalid amount: %s", inbound.Amount)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
			// --- step 3: resolve / deploy UEA
			prc20AddressHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
			chainNamespace, chainId, caipErr := types.ParseCAIP2(inbound.SourceChain)
			if caipErr != nil {
				execErr = fmt.Errorf("invalid SourceChain: %w", caipErr)
				shouldRevert = true
				revertReason = execErr.Error()
			} else {
				universalAccountId := types.UniversalAccountId{
					ChainNamespace: chainNamespace,
					ChainId:        chainId,
					Owner:          inbound.Sender,
				}
				factoryAddress := common.HexToAddress(types.FACTORY_PROXY_ADDRESS_HEX)

				ueaAddr, isDeployed, fErr := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, ueModuleAccAddress, factoryAddress, &universalAccountId)
				if fErr != nil {
					execErr = fmt.Errorf("CallFactory failed: %w", fErr)
					shouldRevert = true
					revertReason = execErr.Error()
				} else {
					if !isDeployed {
						// Deploy new UEA and record a pcTx for it
						deployReceipt, dErr := k.DeployUEAV2(ctx, ueModuleAccAddress, &universalAccountId)
						if dErr != nil {
							execErr = fmt.Errorf("DeployUEA failed: %w", dErr)
							shouldRevert = true
							revertReason = execErr.Error()
						} else {
							// Parse deployed address from return data
							deployedAddr := common.BytesToAddress(deployReceipt.Ret)
							ueaAddr = deployedAddr

							// Record deployment pcTx
							deployPcTx := types.PCTx{
								TxHash:      deployReceipt.Hash,
								Sender:      ueModuleAddressStr,
								BlockHeight: uint64(sdkCtx.BlockHeight()),
								GasUsed:     deployReceipt.GasUsed,
								Status:      "SUCCESS",
							}
							if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
							utx.PcTx = append(utx.PcTx, &deployPcTx)
							return nil
						}); updateErr != nil {
							return updateErr
						}
						}
					}

					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
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
