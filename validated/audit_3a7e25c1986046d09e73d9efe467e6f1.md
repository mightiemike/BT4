## Finding: Silent uint64 truncation of attacker-influenced `GasFee`/`GasLimit` strings in Solana outbound tx building — analogous to the CCIP `dstId` width-mismatch bug

### Summary

The external report's root cause is a **fixed-width integer decode of a value that Chainlink CCIP encodes at a wider width than the consumer expects**, causing either an outright revert (fund lock) or a silently wrong value. Push Chain has the same class of bug in reverse polarity: several string-encoded `uint256` fields that originate from the EVM gateway contract (`UniversalTxOutboundEvent`) are parsed into Go `uint64` via `strconv.ParseUint(..., 10, 64)` **with the error discarded**, in `universalClient/chains/svm/tx_builder.go`. If the underlying Solidity `uint256` value exceeds `math.MaxUint64`, the Go value silently becomes `0` instead of erroring, corrupting the fee/gas-limit value baked into the TSS-signed outbound transaction, instead of causing a clean failure.

### Finding Description

`UniversalPayload` and `OutboundTx` carry gas-related quantities (`gas_limit`, `gas_fee`, `max_fee_per_gas`, etc.) as decimal strings representing Solidity `uint256` values: [1](#0-0) 

These strings are produced from real `*big.Int` values decoded off the EVM `UniversalGatewayPC` outbound event: [2](#0-1) [3](#0-2) 

When the Universal Validator's Solana `TxBuilder` builds the outbound transaction (and the TSS-signed message) from this `OutboundTx`, it re-parses the `GasFee` string back into a fixed-width `uint64`, discarding the parse error: [4](#0-3) 

This mirrors the exact bug class in the CCIP report: a value encoded at one integer width (`uint256` on the EVM/Solidity side) is decoded assuming a narrower width (`uint64`) on the consuming side, with no explicit bounds check. In the CCIP report the mismatch caused a hard revert (`abi.decode` panics on overflow); here Go's `strconv.ParseUint` does not panic — it returns an error that is silently ignored via `_`, so the resulting `gasFee` variable is silently zeroed instead of the transaction being rejected.

### Impact Explanation

If `GasFee` (or similarly-parsed `GasLimit`/`fee` values elsewhere in the same file, all following the same `_ = strconv.ParseUint(...)` pattern) exceeds `2^64-1` (achievable because `upc`/PRC20 amounts use 18 decimals — a `gas_fee` as small as ~18.4 tokens denominated in the smallest unit already exceeds `uint64` max), the outbound signing path silently substitutes `0` for the intended fee. The TSS-signed Solana transaction and its accompanying signing hash are then built and finalized with the wrong (zero) fee, while the Push Chain-side ledger/contract state still reflects the original, correct (large) `gas_fee` value that was deducted from the sender. This creates a canonical-state / accounting divergence between what the chain believes was paid (`OutboundTx.gas_fee` on Push Chain) and what is actually reflected in the outbound artifact the Universal Validators broadcast and sign — falling under the allowed "corruption of ... gas fee accounting ... or canonical UniversalTx state" impact.

Unlike the CCIP bug, this does not itself mint or steal principal funds — it corrupts a downstream fee amount that is only meaningful to the relayer/TSS flow — so the severity is materially lower than the CCIP total-fund-loss scenario, but it is the closest reachable structural analog to the reported bug class in this repository.

### Likelihood Explanation

Triggering the oversized `GasFee` value requires an ordinary, unprivileged user's own inbound payload (large `max_fee_per_gas` / `gas_limit` in their own `UniversalPayload`, which are user-supplied `uint256` fields) to cause the resulting `gas_fee` computed by the EVM gateway contract to exceed `2^64-1`. This is plausible under `upc`'s 18-decimal denomination without requiring any privileged actor, satisfying the "unprivileged external attacker" constraint. However, whether this is economically rational for an attacker (since they are the ones paying the oversized fee) is unclear from the scoped code alone, and I could not confirm from the indexed code whether any upstream `ValidateBasic`/execution-time bound already caps `gas_fee` below `2^64` before it reaches the outbound event. Given the indexing limits, I was not able to fully trace every call site that consumes `data.GasFee` from `tx_builder.go` through to the final TSS-signed payload to confirm there is no separate bounds check specific to the SVM outbound path.

### Recommendation

Replace the discarded-error `strconv.ParseUint` calls in `universalClient/chains/svm/tx_builder.go` (and any structurally identical call sites) with explicit error handling that rejects the outbound (or falls back to a safe, auditable failure) rather than silently substituting `0`, and/or enforce a `uint64`-representable upper bound on `gas_fee`/`gas_limit` at the point they are computed/emitted in the EVM gateway contract and in `x/uexecutor/keeper/create_outbound.go`, so a value that cannot survive the downstream `uint64` decode is rejected before an `OutboundTx` is even created.

### Proof of Concept

Not independently executable from the available indexed code — I could not access the full body of `universalClient/chains/svm/tx_builder.go` (only snippets were returned by search) to confirm the complete downstream usage of the truncated `gasFee` variable in the constructed TSS message, nor could I confirm the exact upper-bound validation (if any) applied to `gas_fee` before it is written into the `OutboundTx` and consumed here. A Devin session with full repository/file access would be needed to trace this end-to-end and build a concrete reproduction (craft an inbound whose payload drives the gateway contract's computed `gas_fee` above `2^64-1`, then observe the resulting Solana outbound's fee field).

### Citations

**File:** proto/uexecutor/v1/types.proto (L31-38)
```text
  string value = 2;                  // Amount in upc as string (uint256)
  string data = 3;                    // ABI-encoded calldata
  string gas_limit = 4;             // uint256 as string
  string max_fee_per_gas = 5;       // uint256 as string
  string max_priority_fee_per_gas = 6; // uint256 as string
  string nonce = 7;                 // uint256 as string
  string deadline = 8;              // uint256 as string
  VerificationType v_type = 9; // Type of verification to use before execution
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L14-29)
```go
type UniversalTxOutboundEvent struct {
	TxID            string   // 0x... bytes32
	Sender          string   // 0x... address
	ChainId         string   // destination chain (CAIP-2 string)
	Token           string   // 0x... ERC20 or zero address for native
	Target          string   // 0x-hex encoded bytes (non-EVM recipient)
	Amount          *big.Int // amount of Token to bridge
	GasToken        string   // 0x... token used to pay gas fee
	GasFee          *big.Int // amount of GasToken paid to relayer
	GasLimit        *big.Int // gas limit for destination execution
	Payload         string   // 0x-hex calldata
	ProtocolFee     *big.Int // fee kept by protocol
	RevertRecipient string   // where funds go on full revert
	TxType          TxType   // ← single source of truth from proto
	GasPrice        *big.Int // gas price on destination chain at time of outbound
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

**File:** universalClient/chains/svm/tx_builder.go (L764-768)
```go
	// Parse gas fee from event data
	var gasFee uint64
	if data.GasFee != "" {
		gasFee, _ = strconv.ParseUint(data.GasFee, 10, 64)
	}
```
