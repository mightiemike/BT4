## Analysis

The `tailOff` bug's core pattern — an unprivileged/attacker-controllable actor supplying an **arbitrary target address** into a flow that ends up moving/burning funds **belonging to that address** without the address owner's consent — has a direct analog in Push Chain's `isCEA` inbound-execution path.

### The analog

When an inbound crosschain transaction is marked `IsCEA = true`, the destination `Recipient` is taken **verbatim** from attacker-controlled inbound data (the field set by whoever calls the source-chain gateway contract) with no whitelist or ownership check — it just needs to resolve to a contract with bytecode: [1](#0-0) 

If the resolved address is not a UEA but has code, it's treated as a `isSmartContract` recipient and the module calls `executeUniversalTx` on it using the module account's privileged signer, with `payload` decoded straight from the attacker-supplied `UniversalPayload.Data`: [2](#0-1) 

Critically, after that call succeeds, `DeductGasFeesFromReceipt` is invoked with `recipient = ueaAddr` — i.e., the attacker-chosen address — and it **burns UPC (native gas token) directly out of that address's own account balance** to pay for gas the attacker's payload consumed: [3](#0-2) 

The same pattern repeats in the `GAS_AND_PAYLOAD` variant: [4](#0-3) 

### Why this is analogous to the `tailOff` report

- `tailOff`: arbitrary `_stratUtil`/token address chosen by an unprivileged strategist → `BathToken.rebalance` moves funds it shouldn't touch.
- Push Chain: arbitrary `Recipient` address chosen by an unprivileged inbound-initiator → `DeductGasFeesFromReceipt` burns UPC balance belonging to that arbitrary address, with the amount driven by attacker-controlled `MaxFeePerGas`/`GasLimit` fields in the `UniversalPayload`, and the executed call itself (`executeUniversalTx`) is attacker-chosen calldata run against a contract the attacker doesn't own.

There is no check that the entity submitting the inbound (the attacker on the source chain) has any relationship to, or consent from, the `Recipient` contract before its PC balance is charged. Since many deployed contracts accept arbitrary/fallback calls without reverting, an attacker can trigger this against any contract holding UPC, causing unauthorized, involuntary burn of that contract's funds — a "permanent loss of protocol/user-controlled funds" scenario reachable purely through ordinary unprivileged inbound submission and honest-validator voting (the validators only attest to *what happened* on the source chain, not to the legitimacy of the attacker-chosen `Recipient`).

I don't have enough confidence from the indexed code alone about downstream mitigations (e.g., whether `uregistry`/gateway-side checks restrict which addresses can appear as `Recipient` for CEA, or whether a minimum-balance/allowance gate exists elsewhere) to be certain this is unmitigated in production. Given the repository size limits on indexing, if you want a definitive verdict I'd recommend inspecting `x/uexecutor/keeper/execute_inbound_gas_and_payload.go` and `execute_inbound_funds_and_payload.go` in full, plus any recipient/CEA registry validation code, via a full Devin session with complete file access.

### Title
Attacker-controlled `Recipient` in `isCEA` inbound flow lets unprivileged actors force gas-fee burns against arbitrary Push Chain accounts - (File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/fees.go)

### Summary
Inbound observations with `IsCEA=true` let the transaction originator (on the source chain, hence attacker-controlled) freely choose the Push Chain `Recipient` address and the `UniversalPayload` (calldata, gas limit, max fee). If `Recipient` resolves to any contract with bytecode (not necessarily related to the attacker), `CallExecuteUniversalTx` invokes `executeUniversalTx` on it as the module account, and `DeductGasFeesFromReceipt` subsequently burns UPC from that contract's own balance to cover the attacker-chosen gas cost — without any consent or relationship check between the attacker and the targeted recipient.

### Finding Description
`ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` resolve `ueaAddr` directly from `utx.InboundTx.Recipient` for CEA inbounds [5](#0-4) . If that address is a plain smart contract (not a UEA), the module executes `executeUniversalTx` against it using attacker-supplied calldata (`utx.InboundTx.UniversalPayload.Data`) [6](#0-5) , then charges gas fees to that same recipient's own UPC balance via `DeductGasFeesFromReceipt`, which performs `SendCoinsFromAccountToModule` + `BurnCoins` from `recipient`'s account [7](#0-6) , with the amount computed from attacker-controlled `MaxFeePerGas`/`GasLimit` fields [8](#0-7) . No check verifies that the recipient contract authorized, initiated, or benefits from this action.

### Impact Explanation
An unprivileged attacker who merely submits an inbound crosschain transaction (through ordinary source-chain gateway usage, truthfully relayed by honest Universal Validators) can force unauthorized burns of native UPC balance from any contract address they choose, as long as that contract does not revert on the low-level `executeUniversalTx` call (e.g., it has a permissive fallback). This is an unauthorized burn of protocol/user-controlled funds, directly reachable by an ordinary unprivileged user with no relayer, validator, or admin compromise required.

### Likelihood Explanation
Likelihood is high for repeated griefing/draining: any contract holding a UPC balance without a code path that reverts unknown calls is a viable target, and the attacker fully controls `GasLimit`/`MaxFeePerGas` to maximize the amount burned per inbound, and can repeat the attack across multiple inbounds/source chains at low cost.

### Recommendation
- Do not deduct gas fees from an arbitrary, attacker-chosen `Recipient` unless that recipient has explicitly opted in to be a CEA execution target (e.g., via a registry of CEA-enabled contracts analogous to token/chain registries already in `x/uregistry`).
- Alternatively, charge gas costs to a protocol-controlled fee pool or to the sender's own UEA rather than the arbitrary contract recipient, and only deduct from the recipient when the recipient contract itself explicitly signals acceptance (e.g., returns a specific value from `executeUniversalTx`).

### Proof of Concept
1. Deploy or identify any contract on Push Chain with a non-reverting fallback/receive function that already holds a UPC balance.
2. On a supported source chain, call the bridge/gateway contract, crafting an inbound with `TxType=FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`), `IsCEA=true`, `Recipient=<victim contract address>`, and a `UniversalPayload` with attacker-chosen `Data`, high `GasLimit`, and `MaxFeePerGas`.
3. Once honest Universal Validators reach quorum and relay the inbound, `ExecuteInboundFundsAndPayload` calls `executeUniversalTx` on the victim contract (succeeding via its permissive fallback) and then burns UPC from the victim contract's balance via `DeductGasFeesFromReceipt`, with no consent from the victim.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L59-88)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-256)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

			amount := new(big.Int)
			amount, ok := amount.SetString(utx.InboundTx.Amount, 10)
			if !ok {
				contractErr = fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
			} else {
				txId := common.HexToHash(utx.Id)

				var payload []byte
				if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
					payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
				}

				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
			}
```

**File:** x/uexecutor/keeper/fees.go (L16-37)
```go
// DeductAndBurnFees deducts gas fees from the user's smart account and burns them.
// The process happens in two steps:
// 1. Transfer coins from user account to module account
// 2. Burn coins from module account
// Returns error if either transfer or burn fails
func (k Keeper) DeductAndBurnFees(ctx context.Context, from sdk.AccAddress, gasCost *big.Int) error {
	amt := sdkmath.NewIntFromBigInt(gasCost)
	coin := sdk.NewCoin(pchaintypes.BaseDenom, amt)

	k.Logger().Debug("deducting and burning fees",
		"from", from.String(),
		"gas_cost", gasCost.String(),
		"denom", pchaintypes.BaseDenom,
	)

	err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, from, types.ModuleName, sdk.NewCoins(coin))
	if err != nil {
		return err
	}

	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(coin))
}
```

**File:** x/uexecutor/keeper/fees.go (L93-140)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L216-256)
```go
	// Smart contract path (isCEA): call executeUniversalTx and return
	if isSmartContract {
		prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

		scAmount := new(big.Int)
		scAmount, ok := scAmount.SetString(utx.InboundTx.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
		}

		txId := common.HexToHash(utx.Id)

		var payload []byte
		if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
			payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
		}

		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)

		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```
