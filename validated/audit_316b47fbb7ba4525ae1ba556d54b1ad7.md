## Analog Identified: Unenforced `TokenConfig.liquidity_cap` allows unbounded PRC20 minting per token, recreating the collateral-concentration risk from the Sherlock report

The external report's root cause is that the protocol has no mechanism bounding how much of any single (potentially depegging) collateral asset can accumulate in the system — one of the report's own recommended fixes was "enforce a ratio / cap between different collateral reserves." Push Chain's `uregistry` module *declares* exactly this safety mechanism (`TokenConfig.liquidity_cap`) but the mint path in `x/uexecutor` never reads or enforces it. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unenforced `TokenConfig.liquidity_cap` allows unbounded PRC20 minting via inbound deposits, defeating the intended per-token exposure cap - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/execute_inbound_gas.go`)

### Summary
`x/uregistry.TokenConfig` carries a mandatory `liquidity_cap` field, validated only for non-emptiness at registration time (`ValidateBasic`), but `x/uexecutor`'s deposit/mint paths (`depositPRC20`, `CallPRC20Deposit`, `CallPRC20DepositAutoSwap`, `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`) never load or check this value before minting PRC20 against an inbound. The cap exists in state and in every token config JSON shipped with the chain, but is structurally decorative.

### Finding Description
Every token config file registered by the admin declares a `liquidity_cap` (e.g. `1000000000000000000000000` in the shipped testnet configs), clearly intended as a ceiling on the total minted supply of that PRC20/native representation: [4](#0-3) 

`TokenConfig.ValidateBasic` only checks the field is a non-empty string — it never parses it as a bound to be compared against anything: [5](#0-4) 

The actual minting logic that credits a user's UEA with PRC20 on a validated inbound (`depositPRC20`) resolves the `TokenConfig` purely to find the `NativeRepresentation.ContractAddress`, parses the amount, and calls `CallPRC20Deposit` — with no comparison of cumulative minted supply (or the current inbound amount) against `TokenConfig.LiquidityCap`: [3](#0-2) 

The same absence holds in the GAS/GAS_AND_PAYLOAD auto-swap routes, which mint and then swap PRC20 for PC via Uniswap V3 with only slippage protection (`minPCOut = quote*95/100`) — no supply cap check at all: [6](#0-5) 

A repo-wide search confirms `LiquidityCap` is referenced only in proto/pb-generated code and test files — it is never read by any keeper logic that performs a mint (`x/uexecutor/keeper/*`), confirming there is no enforcement path anywhere in the mint pipeline.

### Impact Explanation
This directly reproduces the underlying invariant break identified in the external report: the protocol advertises (and an admin configures) a specific ceiling meant to bound exposure to any one external asset/chain, but ordinary unprivileged users bridging funds through the standard inbound flow can mint PRC20 supply for any whitelisted token indefinitely, with no on-chain check against `liquidity_cap`. This defeats the exact "cap-based" mitigation the Sherlock discussion converged on as the correct fix for the collateral-concentration/depeg-contagion problem, and allows unbounded accumulation of a single (possibly depegging or thin-liquidity) token's PRC20 representation in the ecosystem — corrupting PRC20 accounting invariants (actual supply vs. intended cap) and enabling the same downstream harms (draining "good" liquidity via the auto-swap path, over-concentration risk) the original report describes, purely from ordinary/default inbound submission, no privileged actor required.

### Likelihood Explanation
High reachability: any user who can trigger a valid, validator-voted inbound (the standard, expected user flow for bridging any whitelisted asset) hits this path. No malicious validator, admin, or TSS collusion is required — the cap is simply never consulted by honest validators executing the standard deposit/mint logic, so honest nodes will converge on minting past the intended cap deterministically and consistently.

### Recommendation
Track cumulative minted supply per `(chain, token address)` in `x/uexecutor` (or query total PRC20 supply on-chain) and enforce it against `uregistry.TokenConfig.LiquidityCap` before executing `depositPRC20` / `CallPRC20Deposit` / `CallPRC20DepositAutoSwap`. Reject or queue-for-revert any inbound that would push cumulative minted supply for that token above its configured cap, mirroring the `IsChainInboundEnabled`/`IsChainOutboundEnabled` gating pattern already used elsewhere in the module.

### Proof of Concept
1. Admin registers `TokenConfig` for `USDC.eth` with `liquidity_cap = 1_000_000e6`.
2. An external-chain depositor bridges USDC to Push Chain in amounts that, cumulatively, vastly exceed `1_000_000e6` — each inbound individually validates and finalizes normally (honest UV quorum, correct signature/verification data), since nothing in `VoteInbound` → `ExecuteInboundGas`/`depositPRC20` inspects `LiquidityCap`.
3. `CallPRC20Deposit`/`CallPRC20DepositAutoSwap` mints PRC20 for every such inbound without limit, so the token's on-chain PRC20 supply permanently exceeds the value the admin configured as its safety ceiling — with no code path anywhere capable of blocking or even flagging the breach.

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

**File:** x/uregistry/types/token_config.go (L22-68)
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

	if p.NativeRepresentation == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "native_representation is required")
	}
	if err := p.NativeRepresentation.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid native representation")
	}

	return nil
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

**File:** config/testnet-donut/eth_sepolia/tokens/usdc.json (L1-14)
```json
{
  "chain": "eip155:11155111",
  "address": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
  "name": "USDC.eth",
  "symbol": "USDC.eth",
  "decimals": 6,
  "enabled": true,
  "liquidity_cap": "1000000000000000000000000",
  "token_type": 1, 
  "native_representation": {
    "denom": "",
    "contract_address": "0x387b9C8Db60E74999aAAC5A2b7825b400F12d68E"
  }
}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```
