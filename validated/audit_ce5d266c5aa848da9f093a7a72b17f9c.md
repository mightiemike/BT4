### Title
Deposit and outbound paths never check `TokenConfig.Enabled` — a disabled/deprecated token can still be minted and unwound - (File: `x/uexecutor/keeper/handler.go`, `x/uexecutor/keeper/create_outbound.go`)

### Summary
The Morpho report's root cause is a state flag ("deprecated") that is supposed to gate a sensitive action (borrowing) but the code never actually checks it before allowing the action. Push Chain's `uregistry` module has an analogous per-asset flag, `TokenConfig.Enabled` [1](#0-0) , but the flag is never read anywhere in the crosschain execution path.

### Finding Description
`TokenConfig` carries an `Enabled` bool intended to mark whether a given (chain, token) pair is active/whitelisted [2](#0-1) . `AddTokenConfig` / `UpdateTokenConfig` simply persist the struct, including `Enabled`, with no interpretation of the flag [3](#0-2) [4](#0-3) .

However, the actual execution paths that mint or route funds based on a `TokenConfig` never check `Enabled`:

- `depositPRC20`, called from `ExecuteInboundFunds` and `ExecuteInboundFundsAndPayload` on every honest-validator-finalized inbound, fetches the token config and immediately proceeds to mint PRC20 to the recipient — it only checks that `NativeRepresentation` is non-nil, never `tokenConfig.Enabled` [5](#0-4) .
- `BuildOutboundsFromReceipt`, which builds outbound withdrawal instructions from a `UniversalTxOutbound` EVM event, checks `IsChainOutboundEnabled` (chain-level) but never checks the token-level `Enabled` flag on the resolved `TokenConfig` before constructing and attaching the outbound [6](#0-5) .
- `VoteInbound` only enforces `IsChainInboundEnabled` (chain-level gate), never a token-level gate [7](#0-6) .
- The only place `Enabled`/`GetEnabled()` is referenced across the whole repo is in `api/uregistry/v1/types.pulsar.go` (generated protobuf getter, never called by keeper logic), confirmed via a repo-wide grep that turned up no keeper/executor call sites reading `TokenConfig.Enabled` other than protobuf boilerplate.

This mirrors the Morpho pattern exactly: an admin sets a "deprecated"/"disabled" flag on an asset expecting the system to stop processing it, but the flag is dead data — the execution machinery (deposit/mint on inbound, outbound withdrawal construction) has no code path that consults it.

### Impact Explanation
If the admin disables a `TokenConfig` (e.g., because the token is compromised, mis-configured, or being sunset) to stop further deposits/withdrawals, users can still trigger PRC20 minting for that token by depositing on the source chain: an honest validator set will still vote the inbound (chain-level inbound is unaffected), and `depositPRC20` will mint the disabled token's PRC20 representation regardless. Symmetrically, outbound events referencing a disabled token would still be converted into pending outbounds and eventually get TSS-signed and released on the source chain. This breaks the intended admin control surface for asset-level circuit breaking, potentially allowing continued minting/withdrawal of a token the operator explicitly tried to shut off (e.g., due to a known exploit on the source-chain token contract, a bad liquidity cap, or a compromised bridge asset), i.e., unauthorized mint / unauthorized release of protocol-controlled funds using an asset that governance intended to freeze.

### Likelihood Explanation
Reachable by an ordinary unprivileged user: no privileged action beyond the initial admin setting `Enabled=false` (which is the exact governance action the flag exists for) is required to trigger the bug. Any user can send funds on the registered source chain to the gateway for that (now-disabled) token, and honest validators/nodes will process the inbound normally because nothing in `VoteInbound`, `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, or `BuildOutboundsFromReceipt` inspects `TokenConfig.Enabled`. This requires no malicious validator, no key compromise, no external-chain issue — only honest nodes executing the existing code as written.

### Recommendation
Add an explicit `Enabled` check on the resolved `TokenConfig` in:
- `depositPRC20` (`x/uexecutor/keeper/handler.go`) before calling `CallPRC20Deposit`/`CallPRC20DepositAutoSwap`, returning an error such as "token disabled for chain %s" if `!tokenConfig.Enabled`.
- `BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go`) right after resolving `tokenCfg` via `GetTokenConfigByPRC20`, mirroring the existing `IsChainOutboundEnabled` pattern.
- Any other UTX/payload flow that resolves a `TokenConfig` before moving value (e.g., `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`), since these use the same `GetTokenConfig`/`GetTokenConfigByPRC20` lookups.

As with the Morpho recommendation, also verify no already-registered `TokenConfig` is currently `Enabled=false` in a way that would newly reject in-flight inbound/outbound flows once the check is added, to avoid inconsistent-state issues at rollout.

### Proof of Concept
1. Admin calls `MsgAddTokenConfig` with `Enabled=true` for `usdcAddress` on chain `eip155:11155111`, mapping to a PRC20 contract.
2. Admin later calls `MsgUpdateTokenConfig` for the same token with `Enabled=false`, intending to stop further deposits.
3. A user deposits `usdcAddress` funds into the source-chain gateway (no privileged action needed).
4. Honest universal validators observe the event and vote via `VoteInbound`; the vote only checks `IsChainInboundEnabled(eip155:11155111)`, which is still `true`, so the ballot finalizes [7](#0-6) .
5. `ExecuteInboundFunds` → `depositPRC20` fetches the (now `Enabled=false`) `TokenConfig`, finds `NativeRepresentation != nil`, and calls `CallPRC20Deposit`, minting PRC20 to the recipient despite the token being disabled [5](#0-4) .

Note: I was unable to fully trace every call site of `GetTokenConfig`/`GetTokenConfigByPRC20` inside `execute_inbound_gas.go` and `execute_inbound_gas_and_payload.go` end-to-end within available iterations; these likely share the same gap but should be verified directly by a Devin session with full repo access.

### Citations

**File:** x/uregistry/types/token_config.go (L1-20)
```go
package types

import (
	"encoding/json"
	"strings"

	"cosmossdk.io/errors"
	sdkerrors "github.com/cosmos/cosmos-sdk/types/errors"

	"github.com/pushchain/push-chain-node/utils"
)

// Stringer method for TokenConfig
func (p TokenConfig) String() string {
	bz, err := json.Marshal(p)
	if err != nil {
		panic(err)
	}
	return string(bz)
}
```

**File:** x/uregistry/types/token_config_test.go (L22-36)
```go
		{
			name: "valid token config",
			config: types.TokenConfig{
				Chain:                "eip155:1",
				Address:              "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
				Name:                 "USD Coin",
				Symbol:               "USDC",
				Decimals:             6,
				Enabled:              true,
				LiquidityCap:         "1000000000000000000000000",
				TokenType:            types.TokenType_ERC20,
				NativeRepresentation: validNative,
			},
			expectErr: false,
		},
```

**File:** x/uregistry/keeper/msg_add_token_config.go (L10-36)
```go
// AddTokenConfig adds a new token configuration to the uregistry.
func (k Keeper) AddTokenConfig(ctx context.Context, tokenConfig *types.TokenConfig) error {
	// Ensure the chain exists
	if _, err := k.GetChainConfig(ctx, tokenConfig.Chain); err != nil {
		return fmt.Errorf("chain %s is not supported: %w", tokenConfig.Chain, err)
	}

	// More efficient check for existing token config
	storageKey := types.GetTokenConfigsStorageKey(tokenConfig.Chain, tokenConfig.Address)
	has, err := k.TokenConfigs.Has(ctx, storageKey)
	if err != nil {
		return err
	}
	if has {
		return fmt.Errorf("token config for %s on chain %s already exists", tokenConfig.Address, tokenConfig.Chain)
	}

	// Set the new token config
	if err := k.TokenConfigs.Set(ctx, storageKey, *tokenConfig); err != nil {
		return err
	}
	k.Logger().Info("token config added",
		"chain", tokenConfig.Chain,
		"token_address", tokenConfig.Address,
	)
	return nil
}
```

**File:** x/uregistry/keeper/msg_update_token_config.go (L10-29)
```go
// UpdateTokenConfig updates an existing token configuration in the uregistry.
func (k Keeper) UpdateTokenConfig(ctx context.Context, tokenConfig *types.TokenConfig) error {
	storageKey := types.GetTokenConfigsStorageKey(tokenConfig.Chain, tokenConfig.Address)

	// Check if the token config exists
	if has, err := k.TokenConfigs.Has(ctx, storageKey); err != nil {
		return err
	} else if !has {
		return fmt.Errorf("token config for %s on chain %s does not exist", tokenConfig.Address, tokenConfig.Chain)
	}

	if err := k.TokenConfigs.Set(ctx, storageKey, *tokenConfig); err != nil {
		return err
	}
	k.Logger().Info("token config updated",
		"chain", tokenConfig.Chain,
		"token_address", tokenConfig.Address,
	)
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

**File:** x/uexecutor/keeper/create_outbound.go (L49-67)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}
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
