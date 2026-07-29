### Title
Registered `TokenConfig.LiquidityCap` is never enforced during inbound PRC20 minting - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/execute_inbound_funds.go`)

### Summary
`x/uregistry`'s `TokenConfig` carries a `LiquidityCap` field intended as a "max supply cap for this token" [1](#0-0)  and admin tooling requires it to be non-empty at registration time [2](#0-1) . However, in the entire inbound-funds execution path (`depositPRC20` → `CallPRC20Deposit` → `ExecuteInboundFunds`) the field is never read or compared against the cumulative minted amount before minting PRC20 to a recipient [3](#0-2) [4](#0-3) . I was also unable to find any Solidity-side enforcement of this cap — the `PRC20.sol` contract source is not present in the indexed portion of the repository, so I cannot confirm whether the EVM-side contract enforces the cap independently.

### Finding Description
This is the same invariant class as the "Undefined Order For Token Distribution" report: a hard-coded/configured cap on how much of an asset can be minted, where the enforcement of that cap is assumed to happen somewhere but is not actually guaranteed on-chain in the code path reachable by ordinary users. In the Mythos case, the *order* of processing determined whether the cap was respected fairly. In Push Chain's `x/uexecutor`, the situation is more severe: there is no cap check at all in the Go keeper code that handles inbound deposits.

Every registered token (`TokenConfig`) declares a `LiquidityCap`, presumably meant to bound how much of the corresponding PRC20 can ever be minted on Push Chain from a given external-chain asset [5](#0-4) . When an inbound is voted through by validators, `ExecuteInboundFunds` calls `depositPRC20`, which looks up the `TokenConfig` purely to resolve the native PRC20 contract address, and never inspects `LiquidityCap` [6](#0-5) . The mint amount is taken directly from the attacker-controlled/user-supplied `inbound.Amount` field, parsed as a raw `big.Int` with no upper bound check against the cap [7](#0-6) .

Because the amount minted is ultimately whatever was deposited/observed on the external gateway (an event any unprivileged user can trigger by sending funds to the gateway contract), and since Universal Validators just vote on the *observed* event rather than validating it against `LiquidityCap`, an ordinary user can mint PRC20 in excess of the intended supply cap simply by depositing more than the cap on the source chain. This corrupts the intended token-supply invariant and the semantics that downstream consumers (dashboards, swap pools, other protocols) rely on when trusting `LiquidityCap` as an actual ceiling.

### Impact Explanation
If `LiquidityCap` is meant to be a hard ceiling backing risk/collateral assumptions (e.g., how much of a wrapped asset can exist on Push Chain, bounding exposure for swap pools or other protocol logic keyed off PRC20 supply), an unenforced cap allows unauthorized over-minting of PRC20 tokens beyond the declared limit for that token/chain pair — a direct violation of "corruption of PRC20 ... accounting" and "unauthorized mint" in the impact gate. This is reachable by any external, unprivileged user simply by depositing on the source chain; no privileged actor or validator collusion is required, since validators only attest to what was observed, not to whether it violates the registry's configured cap.

### Likelihood Explanation
Likelihood is high in principle, but I could not fully verify whether the `LiquidityCap` field is purely informational (e.g., for off-chain risk dashboards, an off-chain safety check performed by admins before whitelisting a token, or an oracle input) or whether it is genuinely intended as an on-chain enforced ceiling. I was not able to locate the `PRC20.sol` contract source within the indexed content, so I cannot rule out that liquidity cap enforcement is implemented at the EVM contract layer (outside the Go `x/` modules) rather than in the Cosmos keeper. If enforcement exists in the Solidity contract, the Go-side gap may simply reflect a design where uregistry stores metadata and the contract is the actual gate — in which case this finding would not hold. This uncertainty should be resolved before treating this as a confirmed vulnerability.

### Recommendation
- Confirm whether `PRC20.sol` (or the `UniversalCore` handler contract) enforces `liquidityCap` against total supply on every `depositPRC20Token`/`depositPRC20WithAutoSwap` call.
- If no on-chain enforcement exists anywhere (Go keeper or Solidity), add a check in `depositPRC20`/`ExecuteInboundFunds` (and the CEA/gas-and-payload equivalents) that reads `TokenConfig.LiquidityCap`, queries current PRC20 total supply via the EVM, and rejects/reverts the inbound (routing to the revert/refund path) if minting would exceed the cap.
- If the field is genuinely just informational to date, either remove it from `TokenConfig` to avoid misleading operators/integrators, or explicitly document that it is unenforced and add enforcement in a follow-up.

### Proof of Concept
1. Admin registers a token via `MsgAddTokenConfig` with `liquidity_cap = "1000000000000000000000000"` (1,000,000 units at 18 decimals) for `eip155:X` / `USDC` [5](#0-4) .
2. An unprivileged user deposits an amount far exceeding the cap (e.g., 10,000,000 units) into the external gateway contract on `eip155:X`.
3. Universal Validators observe the real on-chain event and vote `MsgVoteInbound` with `Amount = "10000000000000000000000000"` — nothing in `VoteInbound` or the ballot checks the amount against `TokenConfig.LiquidityCap` [8](#0-7) .
4. Once 2/3+ votes pass, `ExecuteInboundFunds` calls `depositPRC20` with the full observed amount, and `CallPRC20Deposit` mints that amount to the recipient's UEA with no cap check [7](#0-6) [3](#0-2) .
5. Total PRC20 supply for that token now exceeds the declared `LiquidityCap` by 9x, with no on-chain rejection.

**Caveat:** step 4's outcome (successful, unbounded mint) assumes the `PRC20.sol`/`UniversalCore` EVM contracts also do not enforce the cap. I could not verify this in the available indexed contract source, so this should be validated with a Devin session that has full repository/contract access before treating this as a confirmed, exploitable bug.

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
