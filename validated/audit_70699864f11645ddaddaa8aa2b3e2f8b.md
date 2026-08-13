### Title
Missing length check on `DestExecData` causes runtime panic in `ExtraDataDecoder.DecodeDestExecDataToMap` - ([File: core/capabilities/ccip/ccipsolana/extradatacodec.go])

### Summary
`ExtraDataDecoder.DecodeDestExecDataToMap` calls `binary.BigEndian.Uint32(destExecData)` without validating that `destExecData` is at least 4 bytes long. Since `TokenAmounts[i].DestExecData` originates from an untrusted CCIP source-chain message and is passed unchecked through `MessageHasherV1.Hash` and `ExecutePluginCodecV1.Encode`, an attacker who controls a CCIP message routed through the Solana-family codec can supply a `DestExecData` shorter than 4 bytes (including empty) to trigger a panic.

### Finding Description
`ExtraDataDecoder.DecodeDestExecDataToMap` in `core/capabilities/ccip/ccipsolana/extradatacodec.go` is: [1](#0-0) 
It directly indexes into `destExecData` via `binary.BigEndian.Uint32` with no length guard, unlike the sibling function `DecodeExtraArgsToMap` in the same file, which explicitly checks `len(extraArgs) < 4` before slicing: [2](#0-1) 

This function is reachable via the `SourceChainExtraDataCodec` interface's `DecodeDestExecDataToMap` method, invoked through `ExtraDataCodecRegistry.DecodeTokenAmountDestExecData` (which just delegates without validation): [3](#0-2) 

Two call sites feed attacker-controlled, unchecked `DestExecData` bytes into this path:
1. `MessageHasherV1.Hash`, which iterates `msg.TokenAmounts` and calls `h.extraDataCodec.DecodeTokenAmountDestExecData(ta.DestExecData, ...)` with no prior length validation of `ta.DestExecData`: [4](#0-3) 
2. `ExecutePluginCodecV1.Encode`, which iterates `msg.TokenAmounts` from the report and calls `e.extraDataCodec.DecodeTokenAmountDestExecData(tokenAmount.DestExecData, ...)`, again without checking the length beforehand: [5](#0-4) 

Neither caller nor the registry nor the codec itself validates `len(destExecData) >= 4` before the `binary.BigEndian.Uint32` call, so a `DestExecData` value of length 0–3 causes an out-of-range slice access panic inside `encoding/binary`.

### Impact Explanation
A panic inside `MessageHasherV1.Hash` (used during OCR report generation/validation for CCIP messages) or `ExecutePluginCodecV1.Encode` (used during execute-plugin report encoding) crashes the goroutine processing that CCIP plugin instance for the affected source chain, unless recovered by a higher-level goroutine wrapper. This matches a node-wide/plugin-wide denial-of-service impact for the CCIP execute/commit plugin handling messages from that source chain, since a single malformed `TokenAmounts[i].DestExecData` on an OnRamp message routed through the Solana destination-family codec can repeatedly trigger the crash whenever that message is processed.

### Likelihood Explanation
The precondition is that a message using the Solana-family `ExtraDataDecoder` codec (registered per source-chain-family combination) carries a `TokenAmounts[i].DestExecData` value shorter than 4 bytes. Since `DestExecData` is a byte field originating from CCIP message data (attacker-influenced via the source chain OnRamp / token pool interactions feeding token transfer data), and no upstream validation enforces minimum length before reaching this codec, the panic is straightforward and repeatable to trigger for any attacker able to originate a CCIP token transfer message routed to a Solana destination.

### Recommendation
Add a length check at the top of `DecodeDestExecDataToMap` mirroring `DecodeExtraArgsToMap`'s pattern, e.g.:
```go
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
    if len(destExecData) < 4 {
        return nil, fmt.Errorf("dest exec data too short: %d, should be at least 4 bytes", len(destExecData))
    }
    return map[string]any{
        svmDestExecDataKey: binary.BigEndian.Uint32(destExecData),
    }, nil
}
```

### Proof of Concept
Unit test in `core/capabilities/ccip/ccipsolana/extradatacodec_test.go`:
```go
func TestDecodeDestExecDataToMap_ShortInput(t *testing.T) {
    d := ExtraDataDecoder{}
    for _, tc := range [][]byte{nil, {}, {0x01}, {0x01, 0x02}, {0x01, 0x02, 0x03}} {
        _, err := d.DecodeDestExecDataToMap(tc)
        require.Error(t, err, "expected graceful error for input %v, got panic instead", tc)
    }
}
```
Expected current behavior: the call panics inside `binary.BigEndian.Uint32` rather than returning an error, demonstrating the missing bounds check. A fuzz test over `destExecData` lengths 0-3 should assert no panic occurs and a non-nil error is returned instead.

### Citations

**File:** core/capabilities/ccip/ccipsolana/extradatacodec.go (L36-39)
```go
func (d ExtraDataDecoder) DecodeExtraArgsToMap(extraArgs cciptypes.Bytes) (map[string]any, error) {
	if len(extraArgs) < 4 {
		return nil, fmt.Errorf("extra args too short: %d, should be at least 4 (i.e the extraArgs tag)", len(extraArgs))
	}
```

**File:** core/capabilities/ccip/ccipsolana/extradatacodec.go (L85-90)
```go
// DecodeDestExecDataToMap is a helper function for converting dest exec data bytes into map[string]any
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
	return map[string]any{
		svmDestExecDataKey: binary.BigEndian.Uint32(destExecData),
	}, nil
}
```

**File:** core/capabilities/ccip/common/extradatacodecregistry.go (L76-83)
```go
func (r *ExtraDataCodecRegistry) DecodeTokenAmountDestExecData(
	destExecData cciptypes.Bytes,
	sourceChainSelector cciptypes.ChainSelector,
) (map[string]any, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.extraDataCodec.DecodeTokenAmountDestExecData(destExecData, sourceChainSelector)
}
```

**File:** core/capabilities/ccip/ccipsolana/msghasher.go (L58-63)
```go
	for _, ta := range msg.TokenAmounts {
		destExecDataDecodedMap, err := h.extraDataCodec.DecodeTokenAmountDestExecData(ta.DestExecData, msg.Header.SourceChainSelector)
		if err != nil {
			return [32]byte{}, fmt.Errorf("failed to decode dest exec data: %w", err)
		}

```

**File:** core/capabilities/ccip/ccipsolana/executecodec.go (L70-73)
```go
			destExecDataDecodedMap, err := e.extraDataCodec.DecodeTokenAmountDestExecData(tokenAmount.DestExecData, chainReport.SourceChainSelector)
			if err != nil {
				return nil, fmt.Errorf("failed to decode dest exec data: %w", err)
			}
```
