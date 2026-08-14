### Title
Missing length validation in `ccipsolana.ExtraDataDecoder.DecodeDestExecDataToMap` causes panic on malformed `TokenAmounts[i].DestExecData` reachable via `decodeExecData` - ([File: core/capabilities/ccip/ccipsolana/extradatacodec.go])

### Summary
`ccipsolana.ExtraDataDecoder.DecodeDestExecDataToMap` calls `binary.BigEndian.Uint32(destExecData)` with no length check, unlike its EVM/Aptos counterparts which validate length and return an error. `decodeExecData` in `core/capabilities/ccip/ocrimpls/svm_contract_transmitter.go` invokes this via `codec.DecodeTokenAmountDestExecData` (routed by `report.AbstractReports[0].SourceChainSelector`) with no `recover()`, so a `TokenAmounts[i].DestExecData` shorter than 4 bytes for a Solana-family source chain will panic instead of returning a decode error.

### Finding Description
`decodeExecData` (`core/capabilities/ccip/ocrimpls/svm_contract_transmitter.go:126-153`) is called from `SVMExecCalldataFunc` after `ccipocr3.DecodeExecuteReportInfo` has already parsed `report.Info` into an `ExecuteReportInfo` struct — that decode step does not validate the semantic contents of `ExtraArgs`/`DestExecData`, only that the outer structure is well formed [1](#0-0) . `decodeExecData` then iterates `message.TokenAmounts` and calls `codec.DecodeTokenAmountDestExecData(tokenAmount.DestExecData, report.AbstractReports[0].SourceChainSelector)` [2](#0-1) .

The codec bundle dispatches by chain family derived from `SourceChainSelector` (`core/capabilities/ccip/common/extradatacodecregistry.go:76-83`). When the source chain family is Solana, this resolves to `ccipsolana.ExtraDataDecoder.DecodeDestExecDataToMap`:

```go
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
	return map[string]any{
		svmDestExecDataKey: binary.BigEndian.Uint32(destExecData),
	}, nil
}
``` [3](#0-2) 

`binary.BigEndian.Uint32` indexes `b[0..3]` unconditionally; if `len(destExecData) < 4` (including empty/nil), this panics with an index-out-of-range runtime error rather than returning an error. This contrasts with:
- `ccipevm.ExtraDataDecoder.DecodeDestExecDataToMap`, which uses `abiDecodeUint32` and returns an error on malformed input [4](#0-3) .
- `ccipaptos.ExtraDataDecoder.DecodeDestExecDataToMap`, which explicitly checks `des.Remaining() != 4` before decoding and returns an error [5](#0-4) .

No `recover()` guards this call path (confirmed absent in `core/capabilities/ccip/**`), so the panic propagates up through `decodeExecData` → `SVMExecCalldataFunc` → the OCR transmit/report-to-calldata pipeline that builds `SVMExecCallArgs` before `ContractWriter.SubmitTransaction`.

### Impact Explanation
A malformed/short `DestExecData` for a Solana-sourced message reaching this code path causes an unrecovered panic in the calldata-construction routine used prior to on-chain transaction submission. Depending on where this executes in the OCR/report-transmission goroutine, this can crash that goroutine (denial of service for the execute-report transmission flow for that job/plugin instance), preventing legitimate execute transactions from being submitted. This is a data-integrity/availability issue (uncontrolled panic on attacker-influenced report field), matching a "misreporting/data tampering causing node malfunction" class of impact rather than fund loss or privilege escalation, since it does not corrupt or misdirect a successfully-submitted on-chain call — it prevents submission via a crash instead.

### Likelihood Explanation
The precondition is that a `RampTokenAmount.DestExecData` value shorter than 4 bytes exists for a message whose `SourceChainSelector` belongs to the Solana chain family, and that this message reaches the OCR execute report (`AbstractReports[0].Messages[0].TokenAmounts[i]`). Whether an ordinary unprivileged user (not a node operator) can directly control `DestExecData` end-to-end (e.g., via the source-chain token pool's `destGasAmount`/`destExecData` derivation during a normal `ccipSend`) could not be fully verified from the indexed code alone; the value is typically produced by token pool logic rather than being raw arbitrary user input, but this repo does not show sufficient validation guarantees at pool-encoding time to rule out a short encoding being produced/forwarded (e.g., through a misconfigured or malicious custom token pool, or a bug on the source-chain encoding side) and passed through to the OCR report undetected by `ccipocr3.DecodeExecuteReportInfo`. This uncertainty limits confidence in real-world unprivileged reachability, but the codec-level defect itself is unambiguous and directly reproducible via unit test.

### Recommendation
Add the same length validation to `ccipsolana.ExtraDataDecoder.DecodeDestExecDataToMap` that exists in the EVM and Aptos implementations, e.g.:
```go
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
	if len(destExecData) != 4 {
		return nil, fmt.Errorf("dest exec data invalid length: %d, should be 4 bytes", len(destExecData))
	}
	return map[string]any{
		svmDestExecDataKey: binary.BigEndian.Uint32(destExecData),
	}, nil
}
```
Additionally, wrap `decodeExecData`/`SVMExecCalldataFunc` (and equivalent calldata-construction functions for other chain families) with a `recover()` at the top-level OCR transmit call site so that any residual decoder panic is converted into a returned error instead of crashing the reporting goroutine.

### Proof of Concept
Unit test to add to `core/capabilities/ccip/ccipsolana/extradatacodec_test.go`:
```go
func TestDecodeDestExecDataToMap_ShortInput_ShouldNotPanic(t *testing.T) {
	d := ExtraDataDecoder{}
	for _, tc := range [][]byte{nil, {}, {0x01}, {0x01, 0x02}, {0x01, 0x02, 0x03}} {
		require.NotPanics(t, func() {
			_, err := d.DecodeDestExecDataToMap(tc)
			require.Error(t, err)
		})
	}
}
```
Integration-level PoC extending `TestSVMExecCallDataFuncExtraDataDecoding` in `core/capabilities/ccip/ocrimpls/contract_transmitter_test.go`: construct an `ExecutePluginReportSingleChain` with `SourceChainSelector` set to a Solana chain selector and `TokenAmounts: []ccipocr3.RampTokenAmount{{DestExecData: []byte{0x01, 0x02}}}`, encode it via `ExecuteReportInfo.Encode()`, and call `ocrimpls.SVMExecCalldataFunc(...)` with an `extraDataCodec` bundle that maps the Solana family to `ccipsolana.ExtraDataDecoder{}`. Assert the call returns an error (`"failed to decode token amount dest exec data"`) rather than panicking (e.g., wrap the call in `require.NotPanics`).

### Citations

**File:** core/capabilities/ccip/ocrimpls/svm_contract_transmitter.go (L60-73)
```go
	var info ccipocr3.ExecuteReportInfo
	var extraDataDecoded ccipcommon.ExtraDataDecoded
	if len(report.Info) != 0 {
		info, err = ccipocr3.DecodeExecuteReportInfo(report.Info)
		if err != nil {
			return "", "", nil, fmt.Errorf("failed to decode execute report info: %w", err)
		}
		if extraDataCodec != nil {
			extraDataDecoded, err = decodeExecData(info, extraDataCodec)
			if err != nil {
				return "", "", nil, fmt.Errorf("failed to decode extra data: %w", err)
			}
		}
	}
```

**File:** core/capabilities/ccip/ocrimpls/svm_contract_transmitter.go (L143-149)
```go
	destExecDataDecoded := make([]map[string]any, len(message.TokenAmounts))
	for i, tokenAmount := range message.TokenAmounts {
		destExecDataDecoded[i], err = codec.DecodeTokenAmountDestExecData(tokenAmount.DestExecData, report.AbstractReports[0].SourceChainSelector)
		if err != nil {
			return ccipcommon.ExtraDataDecoded{}, fmt.Errorf("failed to decode token amount dest exec data: %w", err)
		}
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

**File:** core/capabilities/ccip/ccipevm/extradatacodec.go (L25-35)
```go
// DecodeDestExecDataToMap reformats bytes into a chain agnostic map[string]interface{} representation for dest exec data
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
	destGasAmount, err := abiDecodeUint32(destExecData)
	if err != nil {
		return nil, fmt.Errorf("decode dest gas amount: %w", err)
	}

	return map[string]any{
		evmDestExecDataKey: destGasAmount,
	}, nil
}
```

**File:** core/capabilities/ccip/ccipaptos/extradatadecoder.go (L34-47)
```go
// DecodeDestExecDataToMap reformats bytes into a chain agnostic map[string]interface{} representation for dest exec data
func (d ExtraDataDecoder) DecodeDestExecDataToMap(destExecData cciptypes.Bytes) (map[string]any, error) {
	des := bcs.NewDeserializer(destExecData)
	if des.Remaining() != 4 {
		return nil, fmt.Errorf("dest exec data invalid length: %d, should be 4 bytes", des.Remaining())
	}

	destGasAmount := des.U32()
	if des.Error() != nil {
		return nil, fmt.Errorf("decode dest gas amount: %w", des.Error())
	}

	return map[string]any{aptosDestExecDataKey: destGasAmount}, nil
}
```
