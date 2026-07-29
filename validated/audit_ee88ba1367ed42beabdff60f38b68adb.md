## Analog Found: `TokenConfig.Enabled` flag is not enforced on the inbound minting path

### Title
Disabled `TokenConfig` entries can still be minted/deposited via `MsgVoteInbound` → `ExecuteInboundFunds` — (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/execute_inbound_funds.go`)

### Summary
The Astaria bug is a class of "generic mapping existence check ignores the intended access-restriction flag on the entry" — `vaults[address(vault)] != 0` was used as the sole gate for `lendToVault`, without distinguishing `PrivateVault` from `PublicVault`, even though the whitelisting was clearly meant to be type-restricted. The same class of bug exists in Push Chain's `uregistry`/`uexecutor` interaction: `TokenConfig.Enabled` is explicitly documented and intended as the admin gate for "whether this token is enabled for minting/bridging" [1](#0-0) , but the inbound funds-minting path never reads or checks that field.

### Finding Description
`depositPRC20`, which is the function that actually mints PRC20 for every inbound `FUNDS`/`GAS`/`FUNDS_AND_PAYLOAD` type, only checks that a `TokenConfig` *exists* (via `GetTokenConfig`) and that it has a `NativeRepresentation` — it never inspects `tokenConfig.Enabled`: [2](#0-1) 

This is called directly from `ExecuteInboundFunds`, which is reached purely through the honest-validator ballot-finalization path on `MsgVoteInbound` — no admin/privileged action required by the attacker: [3](#0-2) 

By contrast, chain-level enablement (`ChainConfig.Enabled.IsInboundEnabled`/`IsOutboundEnabled`) *is* explicitly checked before any state changes in `VoteInbound`: [4](#0-3) 

and in `uregistry`'s own keeper helpers: [5](#0-4) 

But there is no equivalent `IsTokenEnabled`-style check anywhere in the token-config lookup used by the deposit/minting flow. Only two files in `x/uexecutor` reference `.Enabled` at all (`msg_execute_payload.go`, `msg_migrate_uea.go`), and neither is the deposit path — confirming the `TokenConfig.Enabled` flag is dead for the purpose the documentation ("Whether this token is enabled for minting/bridging") claims it serves.

### Impact Explanation
An admin who removes/disables a compromised or deprecated token by setting `TokenConfig.Enabled = false` (rather than fully removing the config, e.g. to preserve historical liquidity-cap bookkeeping or pending state) does not actually stop new mints. Any unprivileged relayer/observer who gets an inbound event voted to quorum for that (still-registered-but-disabled) token/chain pair can still trigger `depositPRC20`, causing unauthorized PRC20 minting exactly as if the token were still enabled — a direct violation of the registry's admin-curated accounting invariant ("PRC20 or native asset accounting ... must not misroute value"). This is analogous to LPs lending to `PrivateVault`s despite the docs saying only `PublicVault`s should accept outside liquidity: a security-relevant boolean gate that governs "who/what may participate" is bypassed because only presence-in-mapping is checked, not the flag itself.

### Likelihood Explanation
Reaching this requires only the existing, expected honest-validator quorum flow for an inbound event on any (chain, token) pair whose `TokenConfig` row exists with `Enabled=false` — no malicious validator collusion, no privileged key, and no protocol-level bypass is needed, since inbound observation and voting are performed by honest UVs relaying real user-visible chain events. The only precondition is that an admin has toggled `Enabled=false` on an existing `TokenConfig` (a normal, expected operational action) rather than removing it via `MsgRemoveTokenConfig`.

### Recommendation
Add an explicit check of `tokenConfig.Enabled` in `depositPRC20` (or immediately in `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload`) before calling `CallPRC20Deposit`, mirroring the `IsChainInboundEnabled`/`IsChainOutboundEnabled` pattern already used at the chain level, and route the rejection into the same failed-PCTx + revert-outbound flow used for missing token configs.

### Proof of Concept
1. Admin registers `ChainConfig` (`eip155:11155111`, inbound/outbound enabled) and `TokenConfig` for USDC with `Enabled: true`, `NativeRepresentation.ContractAddress = PRC20USDCAddr`.
2. Admin later calls `MsgUpdateTokenConfig` to set `Enabled: false` for that token (intending to halt new bridging of this asset) while leaving the row in place.
3. An unprivileged attacker triggers/observes a real inbound funds event for that token on the source chain (or colludes with no one — any legitimate user's deposit works); Universal Validators (honest, unprivileged w.r.t. this bug) vote `MsgVoteInbound` to quorum as usual, per `TestSolanaInboundFunds`/`TestInboundGas`-style flows shown in the integration tests (e.g. `test/integration/uexecutor/inbound_solana_test.go:138-165`, `test/integration/uexecutor/execute_inbound_gas_test.go`).
4. `ExecuteInboundFunds` → `depositPRC20` → `GetTokenConfig` succeeds (row exists) and mints PRC20 to the recipient exactly as if `Enabled` were `true`, because `Enabled` is never read.
5. Result: minting continues for a token the registry admin explicitly disabled, demonstrating the same "mapping existence used as the sole permission check while ignoring a per-entry restriction flag" root cause as the Astaria M-14 report.

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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}
```

**File:** x/uregistry/keeper/keeper.go (L195-225)
```go
// IsChainInboundEnabled checks if inbound is enabled for a given chain
func (k Keeper) IsChainInboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsInboundEnabled, nil
}

// IsChainOutboundEnabled checks if outbound is enabled for a given chain
func (k Keeper) IsChainOutboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsOutboundEnabled, nil
}
```
