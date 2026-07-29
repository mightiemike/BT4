## Analysis: L1 Rollup Fee Not Charged for Outbound Transactions to OP-Stack Destination Chains

The Sherlock H-6 root cause — compensating an L2 broadcaster based only on L2 execution gas while ignoring the separate L1 data-availability fee that OP-stack chains charge — has a direct structural analog in Push Chain's **outbound withdrawal path**, and the codebase itself proves the fix pattern exists but was applied inconsistently.

### Title
Outbound withdrawal `GasFee` charged to users omits L1 data-availability fee for OP-stack destination chains, causing systematic TSS/protocol fund shortfall - (File: `x/uexecutor/keeper/gas_fee.go`, `x/uexecutor/keeper/create_outbound.go`, `universalClient/chains/evm/tx_builder.go`)

### Summary
Push Chain already recognizes that OP-stack chains (Optimism/Base-style L2s) require an additional L1 rollup/data-availability fee on top of L2 execution gas — this is explicitly modeled and added for the **TSS fund-migration** sweep path via `GetL1GasFeeByChain` / `l1GasFeeByChainNamespace` and `computeFundMigrationTransfer`, which adds `l1GasFee` on top of `gasPrice * gasLimit` [1](#0-0) . However, the parallel and far more frequently used **outbound withdrawal** path never applies this same correction.

### Finding Description
`GetOutboundTxGasAndFees` computes the fee the user pays for an outbound by calling `UniversalCore.getOutboundTxGasAndFees(prc20, gasLimit)`, returning `gasToken`, `gasFee`, `protocolFee`, `gasPrice`, and `gasLimitUsed` [2](#0-1) . This `gasFee` (only `gasPrice * gasLimit`-derived, per the ABI signature) is what's collected from the user and stored on the `OutboundTx` record when the outbound event is emitted from the gateway contract [3](#0-2) .

When the TSS signer actually builds and broadcasts the outbound transaction to the destination chain via `GetOutboundSigningRequest` / `BroadcastOutboundSigningRequest`, it only ever uses `gasPrice * gasLimit` from that event data — there is no L1 fee term anywhere in this path [4](#0-3) .

Compare this to the fund-migration sweep, which explicitly reads `L1GasFee` from the chain registry and subtracts it in `computeFundMigrationTransfer`, with an inline comment: *"L1GasFee covers OP-stack sequencer data-availability charges; 0 for non-L2 chains"* [5](#0-4) . The same `GetL1GasFeeByChain` keeper method already exists on `x/uexecutor` and is wired only into `x/utss`'s `InitiateFundMigration` [6](#0-5)  — it is never consulted when building the `gasFee` charged for ordinary outbound withdrawals, nor when the excess-gas refund is computed against the observed `gasFeeUsed` in `applyGasRefund` [7](#0-6) .

### Impact Explanation
Every outbound withdrawal (funds, funds+payload, gas+payload, inbound-revert, rescue-funds — any `TxType` that produces a destination-chain broadcast) to an OP-stack-style destination chain will collect from the user only the L2 execution portion of the real on-chain cost. The L1 data-availability fee the TSS signer actually pays to broadcast the transaction is never recovered from the user and never modeled anywhere in the outbound gas-fee lifecycle (`GetOutboundTxGasAndFees` → `OutboundTx.GasFee` → `applyGasRefund`'s excess-refund comparison). This is a corruption of the outbound gas-fee accounting invariant (the collected `GasFee` structurally understates the true broadcast cost for a whole class of destination chains), causing the protocol/TSS custody funds to be systematically drained to subsidize every outbound withdrawal to those chains — a version of the "material, low-cost fund loss reachable by ordinary unprivileged users" impact described in H-6, just realized as unrecovered protocol cost instead of keeper cost.

### Likelihood Explanation
This triggers on every single outbound withdrawal to any configured OP-stack destination chain (e.g. Optimism, Base) — it requires no adversarial input, only a normal user-initiated withdrawal, and reproduces on 100% of such outbounds, not an intermittent gas-ratio condition like the original report. The chain registry already tracks per-chain `L1GasFee` (via `l1GasFeeByChainNamespace`), so any chain marked as an OP-stack-style L2 with a non-zero configured L1 fee is affected each time an outbound is created for it.

### Recommendation
Extend `GetOutboundTxGasAndFees` (and/or the `UniversalCore.getOutboundTxGasAndFees` contract call it wraps) to add the chain's `L1GasFee` (already retrievable via `GetL1GasFeeByChain`) into the collected `gasFee`, mirroring the pattern already implemented in `computeFundMigrationTransfer`. Also thread the L1 fee into `applyGasRefund`'s excess-fee comparison against `obs.GasFeeUsed` reported by validators so refunds correctly reconcile L1 + L2 cost, not just L2 cost.

### Proof of Concept
1. Configure a destination chain namespace with `setL1GasFeeByChain(chain, nonZeroFee)` on `UniversalCore` (the same setup used in `test/integration/utss/fund_migration_test.go`'s `seedFundMigrationChainValues`).
2. Trigger a normal outbound (e.g. a `FUNDS` withdrawal) to that chain. `BuildOutboundsFromReceipt` records `outbound.GasFee = event.GasFee`, which is `gasPrice * gasLimit` only, per `getOutboundTxGasAndFees`'s ABI (`gasToken, gasFee, protocolFee, gasPrice, chainNamespace, gasLimitUsed` — no L1 fee output field) [8](#0-7) .
3. The TSS coordinator/broadcaster signs and sends the transaction using `GetOutboundSigningRequest`/`BroadcastOutboundSigningRequest`, which likewise only use `gasPrice`/`gasLimit`, never `L1GasFee` [9](#0-8) .
4. On the real destination chain, the actual transaction cost is `L2Fee + L1Fee`; the user's payment only covered `L2Fee`. The TSS wallet's balance is depleted by the uncompensated `L1Fee` on every outbound, exactly analogous to the keeper compensation gap in H-6, but here manifesting as an unrecovered protocol/TSS custody cost rather than a keeper's personal loss.

### Citations

**File:** universalClient/chains/evm/tx_builder.go (L83-154)
```go
func (tb *TxBuilder) GetOutboundSigningRequest(
	ctx context.Context,
	data *uetypes.OutboundCreatedEvent,
	nonce uint64,
) (*common.UnsignedSigningReq, error) {
	if data == nil {
		return nil, fmt.Errorf("outbound event data is nil")
	}
	if data.TxID == "" {
		return nil, fmt.Errorf("txID is required")
	}
	if data.DestinationChain == "" {
		return nil, fmt.Errorf("destinationChain is required")
	}

	gasPrice := new(big.Int)
	if data.GasPrice != "" {
		if _, ok := gasPrice.SetString(data.GasPrice, 10); !ok {
			return nil, fmt.Errorf("invalid gas price in event data: %s", data.GasPrice)
		}
	}
	if gasPrice.Sign() == 0 {
		return nil, fmt.Errorf("gas price is zero or missing in outbound event")
	}

	gasLimit, err := parseGasLimit(data.GasLimit)
	if err != nil {
		return nil, err
	}

	amount := new(big.Int)
	amount, ok := amount.SetString(data.Amount, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", data.Amount)
	}

	assetAddr := ethcommon.HexToAddress(data.AssetAddr)

	txType, err := parseTxType(data.TxType)
	if err != nil {
		return nil, fmt.Errorf("invalid tx type: %w", err)
	}

	funcName := tb.determineFunctionName(txType, assetAddr)

	txData, err := tb.encodeFunctionCall(funcName, data, amount, assetAddr, txType)
	if err != nil {
		return nil, fmt.Errorf("failed to encode function call: %w", err)
	}

	txValue := big.NewInt(0)
	if assetAddr == (ethcommon.Address{}) {
		txValue = amount
	}

	tx := types.NewTransaction(
		nonce,
		tb.vaultAddress,
		txValue,
		gasLimit.Uint64(),
		gasPrice,
		txData,
	)

	signer := types.NewEIP155Signer(big.NewInt(tb.chainIDInt))
	txHash := signer.Hash(tx).Bytes()

	return &common.UnsignedSigningReq{
		SigningHash: txHash,
		Nonce:       nonce,
	}, nil
}
```

**File:** universalClient/chains/evm/tx_builder.go (L477-482)
```go
// GetFundMigrationSigningRequest builds a native token transfer for fund migration,
// transferring the maximum possible balance (balance minus gas cost minus L1 fee).
// Fund migration only triggers when outbound is disabled and no pending outbounds remain,
// so the balance at signing time will equal the balance at broadcast time.
// L1GasFee covers OP-stack sequencer data-availability charges; 0 for non-L2 chains.
func (tb *TxBuilder) GetFundMigrationSigningRequest(ctx context.Context, data *common.FundMigrationData, nonce uint64) (*common.UnsignedSigningReq, error) {
```

**File:** universalClient/chains/evm/tx_builder.go (L590-607)
```go
// computeFundMigrationTransfer returns the native amount to sweep from the old
// TSS address to the new one: balance - (gasPrice * gasLimit) - l1GasFee.
// The l1GasFee covers OP-stack sequencer data-availability charges (0 for
// non-L2 chains). All validators must compute the same value — any drift
// here breaks the TSS signing hash.
func computeFundMigrationTransfer(balance, gasPrice *big.Int, gasLimit uint64, l1GasFee *big.Int) (*big.Int, error) {
	gasCost := new(big.Int).Mul(gasPrice, new(big.Int).SetUint64(gasLimit))
	totalFee := new(big.Int).Set(gasCost)
	if l1GasFee != nil && l1GasFee.Sign() > 0 {
		totalFee.Add(totalFee, l1GasFee)
	}
	maxTransfer := new(big.Int).Sub(balance, totalFee)
	if maxTransfer.Sign() <= 0 {
		return nil, fmt.Errorf("insufficient balance for gas: balance=%s gasCost=%s l1GasFee=%s",
			balance.String(), gasCost.String(), l1GasFeeString(l1GasFee))
	}
	return maxTransfer, nil
}
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-64)
```go
// GetOutboundTxGasAndFees calls UniversalCore.getOutboundTxGasAndFees(prc20, gasLimitWithBaseLimit)
// to get gasToken, gasFee, protocolFee, gasPrice, and chainNamespace.
// Pass gasLimitWithBaseLimit=0 to use the contract's baseLimit.
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L80-83)
```go
	l1GasFee, err := k.uexecutorKeeper.GetL1GasFeeByChain(sdkCtx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to get l1 gas fee for chain %s: %w", chain, err)
	}
```

**File:** x/uexecutor/keeper/outbound.go (L174-196)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}
```

**File:** x/uexecutor/types/abi.go (L404-420)
```go
    {
      "type": "function",
      "name": "getOutboundTxGasAndFees",
      "inputs": [
        { "name": "_prc20", "type": "address", "internalType": "address" },
        { "name": "gasLimitWithBaseLimit", "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [
        { "name": "gasToken", "type": "address", "internalType": "address" },
        { "name": "gasFee", "type": "uint256", "internalType": "uint256" },
        { "name": "protocolFee", "type": "uint256", "internalType": "uint256" },
        { "name": "gasPrice", "type": "uint256", "internalType": "uint256" },
        { "name": "chainNamespace", "type": "string", "internalType": "string" },
        { "name": "gasLimitUsed", "type": "uint256", "internalType": "uint256" }
      ],
      "stateMutability": "view"
    },
```
