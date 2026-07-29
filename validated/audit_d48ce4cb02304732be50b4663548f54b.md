The Gateway/vault solidity contracts on the source chain aren't present in this repository's index (they live in a separate contracts repo), so I cannot verify whether the on-chain Gateway performs a strict pre/post balance check like Sandclock's `_transferAndCheckUnderlying()`. What I *can* confirm from this repo is how Push Chain's node consumes the inbound `amount`: it is taken verbatim from the source-chain event's `bridgeAmount` field and used directly to mint/deposit PRC20 tokens, with no on-chain corroboration against the actual custodied balance.

### Title
Inbound deposit accounting trusts the raw `bridgeAmount` event field with no verification against actual custodied collateral - ([File: x/uexecutor/keeper/execute_inbound_funds.go], [File: universalClient/chains/evm/event_parser.go])

### Summary
Push Chain's honest-validator inbound flow extracts the deposit amount solely from the `sendFunds`/`addFunds` gateway event's `bridgeAmount` word and mints that exact amount of PRC20 tokens to the recipient. If the collateral asset locked in the source-chain Gateway is (or becomes) a fee-on-transfer / rebasing / deflationary ERC20, the actual amount the Gateway custodies can be less than the `bridgeAmount` emitted in the event, yet Push Chain still mints the full declared amount.

### Finding Description
`parseUniversalTxEvent` decodes the deposit amount directly from Word 1 of the gateway log data (`payload.Amount = new(big.Int).SetBytes(log.Data[1*32:2*32]).String()`) with no cross-check against the Gateway contract's real token balance delta. [1](#0-0) 

This value flows unmodified into the `Inbound.Amount` field and is passed straight to `depositPRC20` → `CallPRC20Deposit`, which mints PRC20 tokens 1:1 with the event amount, without querying the Gateway's actual token balance on the source chain: [2](#0-1) [3](#0-2) 

Because universal validators only observe and vote on the emitted `bridgeAmount`, not the true balance received by the Gateway contract, this is the Push Chain analog of the referenced Sandclock issue: instead of causing a deposit revert (as in the original vault's `_transferAndCheckUnderlying`), the mismatch here causes silent over-minting, since no equivalent balance-delta check exists on the consuming (Push Chain) side.

### Impact Explanation
If any token onboarded as a bridgeable asset charges a transfer fee, rebases downward, or is later upgraded to add such behavior (as historically happened with USDT-style tokens), every inbound deposit mints PRC20 tokens for the full declared amount while the Gateway's actual custodied collateral is smaller. This breaks the PRC20-to-collateral backing invariant: PRC20 total supply grows faster than the real locked collateral, so eventual outbound/redemption requests can drain the Gateway short, causing insolvency and inability to honor withdrawals for other users. This falls squarely under "corruption of PRC20 or native asset accounting" in the allowed-impact scope.

### Likelihood Explanation
Likelihood is contingent on whether a fee-on-transfer token is ever onboarded via the token-config/registry allowlist (`x/uregistry` `TokenConfig`). If the operator's token vetting process excludes such tokens, likelihood is low; but as the original finding notes, tokens can silently add fee mechanisms later (USDT precedent), meaning a token safe at onboarding time could become unsafe. There is no on-chain code path in the node that detects or rejects this drift.

### Recommendation
- Cross-check the Gateway's on-chain token balance delta (pre/post the locking transaction) against the emitted `bridgeAmount`, either by having the source-chain Gateway contract itself measure actual received balance and emit that in the event (mirroring the pattern `_transferAndCheckUnderlying` was meant to enforce), or by having universal validators independently verify the balance delta before voting to finalize the inbound.
- Restrict onboarded collateral tokens (via `uregistry` token config governance) to non-fee-on-transfer, non-rebasing tokens, and add periodic reconciliation between total PRC20 supply and the Gateway's real balance per source chain/asset.

### Proof of Concept
1. Registry onboards token `T` on source chain `C` as bridgeable collateral via `uregistry` `TokenConfig`.
2. `T` charges (or later adds) a 1% transfer fee. A user calls the Gateway's lock/sendFunds method with `amount = 1000`; the Gateway actually receives only 990 `T` after the fee, but emits the event with `bridgeAmount = 1000` (word 1 of event data), matching the declared input rather than the real balance delta.
3. Universal validators parse this event via `parseUniversalTxEvent`, producing `Inbound.Amount = "1000"` [4](#0-3) , reach quorum, and Push Chain calls `depositPRC20` with `amountStr = "1000"` [5](#0-4) , minting 1000 PRC20 to the recipient although only 990 `T` back it.
4. Over repeated deposits, PRC20 total supply exceeds the Gateway's real `T` balance by the accumulated fee amount, so later outbound redemptions for `T` can fail or drain the Gateway for unrelated users — an accounting corruption reachable purely from an ordinary unprivileged user's deposit.

### Citations

**File:** universalClient/chains/evm/event_parser.go (L169-174)
```go
	// Parse common static fields: token (Word 0), amount (Word 1)
	payload.Token = ethcommon.BytesToAddress(log.Data[0*32+12 : 0*32+32]).Hex()
	payload.Amount = new(big.Int).SetBytes(log.Data[1*32 : 2*32]).String()

	dataOffset := new(big.Int).SetBytes(log.Data[2*32 : 3*32]).Uint64()
	parseUniversalTx(event, log, dataOffset, &payload, logger)
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
