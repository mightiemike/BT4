### Title
Gas-abstraction auto-swap and gas-refund swap derive their slippage bound (`minPCOut`) from the same manipulable spot quote they execute against, allowing pool-price manipulation to drain protocol/user funds during Uniswap V3 gas-token swaps - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The external report's core issue is that Uniswap V3 swaps executed without a pre-existing, trustworthy pool allow an attacker to control the pool price and extract value, because the contract's own slippage protection is computed from the very price the attacker controls. Push Chain's `x/uexecutor` module reproduces this exact pattern natively: every gas-abstraction inbound deposit and every outbound gas refund performs a PRC20→WPC (or reverse) swap through an on-chain Uniswap V3 fork, where the `minPCOut` slippage bound is computed directly from a same-call spot quote (`QuoterV2.quoteExactInputSingle`) rather than any external or time-weighted reference.

### Finding Description
`ExecuteInboundGas` executes for every inbound gas-abstraction deposit of a registered token. It resolves the token's `defaultFeeTier` and fetches a live spot quote, then computes the slippage floor directly from that same quote before letting `depositPRC20WithAutoSwap` execute the real swap: [1](#0-0) 

The quote itself comes from `GetSwapQuote`, a static (`commit=false`) call to `QuoterV2.quoteExactInputSingle`, which reads the pool's current spot price with no TWAP, no minimum-liquidity check, and no verification that the pool being priced is the legitimate, admin-seeded pool: [2](#0-1) 

`CallPRC20DepositAutoSwap` then performs the module-originated `DerivedEVMCall` that actually executes the swap on `UniversalCore.depositPRC20WithAutoSwap`, passing the caller-computed `minPCOut`: [3](#0-2) 

The identical pattern is repeated for outbound gas refunds in `applyGasRefund`/`getSwapQuoteForRefund`, where a refund of unused gas is swapped back to WPC using the same spot-quote-derived bound before calling `CallUniversalCoreRefundUnusedGas`: [4](#0-3) [5](#0-4) 

Because both `defaultFeeTier` and the swap quote are read from whatever pool currently exists at that fee tier — with no enforcement that the pool has real, admin-provisioned liquidity — an attacker who is first to create (or who thinly seeds) a WPC/PRC20 pool at the configured `defaultFeeTier` for a freshly onboarded token controls the price the module trades against. Since `minPCOut = quote * 95 / 100` is derived from that same attacker-controlled quote, the 5% slippage guard provides no real protection: the module will happily execute the swap at the attacker's chosen skewed price, exactly mirroring the `sellProfits`/`Fees.sol` bug class where the "safety check" is computed from the same manipulable price it is supposed to guard against.

### Impact Explanation
This affects module-originated EVM execution (`DerivedEVMCall`, `isModuleSender=true`) that moves protocol- and user-bound value: `depositPRC20WithAutoSwap` swaps a user's deposited PRC20 into WPC to fund their UEA, and `refundUnusedGas` swaps residual gas tokens back to WPC for a refund recipient. An attacker who controls the priced pool at the moment either swap executes can extract WPC that should have gone to the user's UEA or to the refund recipient, i.e., a drain of protocol-held/user-bound funds through an unauthorized-value module-initiated swap — squarely in the "stealing/draining of user or protocol-controlled funds via unauthorized module-originated EVM execution" impact category.

### Likelihood Explanation
This is only exploitable in the window where a token has been registered in `x/uregistry` (`TokenConfigs`) but the corresponding pool for its configured `defaultFeeTier` has not yet been seeded with real, admin-provided liquidity — e.g., immediately after a chain/token is onboarded and before the operational pool-creation step completes, or if `defaultFeeTier` is left at its zero/uninitialized default and a pool at that fee tier doesn't exist yet. Given the project's own e2e tooling explicitly performs "Create WPC pools for all deployed core tokens" as a distinct, separately-ordered setup step from token/chain config registration, there is a real operational sequencing gap during which this condition can occur: [6](#0-5) 
Once such a gap exists, any ordinary user's inbound deposit (or a refund event) for that token triggers the vulnerable swap path automatically — no privileged action by the attacker is required beyond the routine, permissionless act of creating/seeding a Uniswap V3 pool on the target fee tier before the legitimate pool exists.

### Recommendation
Do not derive the swap's slippage floor solely from the same-call spot quote of an unverified pool. Before allowing `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` to execute a swap:
- Require and check a minimum on-chain liquidity threshold for the pool at `defaultFeeTier[prc20]` before trusting `GetSwapQuote`'s output.
- Use a time-weighted average price (TWAP) or an external reference price bound (e.g., compare against `uregistry` token config expected value) rather than a pure spot quote to compute `minPCOut`.
- Gate `defaultFeeTier` configuration so it cannot be left at (or default to) a fee tier with no admin-seeded pool, and reject swaps when the configured fee tier resolves to an unexpected/newly-created pool.
- Sequence token onboarding so pool creation/liquidity seeding is guaranteed to complete and be verified before the token becomes eligible for gas-abstraction auto-swap in `x/uexecutor`.

### Proof of Concept
1. Admin registers a new token config in `x/uregistry` for chain `X` / token `T`, mapping to PRC20 `P`, but has not yet created a real liquidity pool for `P`/`WPC` (or `UniversalCore.defaultFeeTier[P]` is still unset/default).
2. Attacker (unprivileged) creates a Uniswap V3 pool for `P`/`WPC` at the fee tier that will resolve from `GetDefaultFeeTierForToken`, seeding it with minimal liquidity at a heavily skewed price favoring themselves.
3. A user (or the attacker themselves) sends a normal cross-chain deposit of `T`; Universal Validators observe and vote it in via `MsgVoteInbound`, and the core validator calls `ExecuteInboundGas`.
4. `GetDefaultFeeTierForToken` + `GetSwapQuote` (`x/uexecutor/keeper/evm.go:471-538`) return the attacker-controlled pool's skewed spot price; `minPCOut` is computed as 95% of that same skewed quote (`x/uexecutor/keeper/execute_inbound_gas.go:142-146`).
5. `CallPRC20DepositAutoSwap` executes `depositPRC20WithAutoSwap` against the attacker's pool, succeeding because `minPCOut` was derived from (and thus always satisfied by) the attacker's own price, transferring WPC value to the attacker's pool at the deposited user's expense.
6. The identical sequence applies to `applyGasRefund` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas` for outbound gas-refund swaps.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
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

**File:** x/uexecutor/keeper/outbound.go (L213-234)
```go
	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
```

**File:** x/uexecutor/keeper/outbound.go (L259-270)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
}
```

**File:** e2e-tests/setup.sh (L4482-4571)
```shellscript
step_create_all_wpc_pools() {
  require_cmd node cast "$PUSH_CHAIN_DIR/build/pchaind"
  ensure_deploy_file

  [[ -n "${PRIVATE_KEY:-}" ]] || { log_err "Set PRIVATE_KEY in e2e-tests/.env"; exit 1; }

  if [[ ! -f "$TEST_ADDRESSES_PATH" ]]; then
    log_err "Missing test-addresses.json at $TEST_ADDRESSES_PATH"
    exit 1
  fi

  local wpc_addr token_count token_addr token_symbol
  wpc_addr="$(address_from_deploy_contract "WPC")"
  if [[ -z "$wpc_addr" ]]; then
    log_err "Missing WPC contract address in $DEPLOY_ADDRESSES_FILE"
    exit 1
  fi

  token_count="$(jq -r '.tokens | length' "$DEPLOY_ADDRESSES_FILE")"
  if [[ "$token_count" == "0" ]]; then
    log_warn "No core tokens found in deploy addresses; skipping pool creation"
    return 0
  fi

  local deployer_evm_addr
  deployer_evm_addr="$(cast wallet address --private-key "$PRIVATE_KEY" 2>/dev/null || true)"
  if ! validate_eth_address "$deployer_evm_addr"; then
    log_err "Could not resolve deployer EVM address from PRIVATE_KEY"
    exit 1
  fi

  local deployer_hex deployer_push_addr
  deployer_hex="$(echo "$deployer_evm_addr" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')"
  deployer_push_addr="$("$PUSH_CHAIN_DIR/build/pchaind" debug addr "$deployer_hex" 2>/dev/null | awk -F': ' '/Bech32 Acc:/ {print $2; exit}')"
  if [[ -z "$deployer_push_addr" ]]; then
    log_err "Could not derive bech32 deployer address from $deployer_evm_addr"
    exit 1
  fi

  log_info "Funding deployer $deployer_push_addr ($deployer_evm_addr) for pool creation ($POOL_CREATION_TOPUP_AMOUNT)"
  local fund_attempt=1
  local fund_max_attempts=5
  local fund_out=""
  while true; do
    fund_out="$("$PUSH_CHAIN_DIR/build/pchaind" tx bank send "$GENESIS_KEY_NAME" "$deployer_push_addr" "$POOL_CREATION_TOPUP_AMOUNT" \
      --gas-prices "$GAS_PRICES" \
      --keyring-backend "$KEYRING_BACKEND" \
      --chain-id "$CHAIN_ID" \
      --home "$GENESIS_KEY_HOME" \
      -y 2>&1 || true)"

    if echo "$fund_out" | grep -q 'txhash:' || echo "$fund_out" | grep -q '"txhash"'; then
      log_ok "Deployer funding transaction submitted"
      break
    fi

    if echo "$fund_out" | grep -qi 'account sequence mismatch' && [[ "$fund_attempt" -lt "$fund_max_attempts" ]]; then
      log_warn "Funding sequence mismatch on attempt $fund_attempt/$fund_max_attempts. Retrying..."
      fund_attempt=$((fund_attempt + 1))
      sleep 2
      continue
    fi

    log_err "Failed to fund deployer for pool creation"
    echo "$fund_out"
    exit 1
  done
  sleep 2

  while IFS=$'\t' read -r token_symbol token_addr; do
    [[ -n "$token_addr" ]] || continue
    if [[ "$(echo "$token_addr" | tr '[:upper:]' '[:lower:]')" == "$(echo "$wpc_addr" | tr '[:upper:]' '[:lower:]')" ]]; then
      continue
    fi

    local pool_token_amount="1"
    local pool_wpc_amount="4"
    if [[ "$token_symbol" == "pSOL" ]]; then
      pool_token_amount="${LOCAL_PSOL_POOL_TOKEN_AMOUNT:-50}"
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
    (
      cd "$SWAP_AMM_DIR"
      node scripts/pool-manager.js create-pool "$token_addr" "$wpc_addr" 4 500 true "$pool_token_amount" "$pool_wpc_amount"
    )
  done < <(jq -r '.tokens[]? | [.symbol, .address] | @tsv' "$DEPLOY_ADDRESSES_FILE")

  log_ok "All token/WPC pool creation commands completed"
```
