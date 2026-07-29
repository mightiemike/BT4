### Title
Unvalidated `uint64` offset in gateway event ABI decoding causes observer panic on crafted payload data - (File: `universalClient/chains/evm/event_parser.go`)

### Summary
The bug report describes memory-index increments in Rust circuit precompiles (`ecadd.rs`, etc.) that lack explicit overflow/bounds checks on read/write offsets. The concrete Push Chain analog is in the EVM gateway-event decoder used by `universalClient`, where a `uint64` byte offset taken directly from attacker-influenced event data is used in unchecked pointer-style arithmetic (`absOff+32`, `dataStart+byteLen`) before slicing a byte buffer.

### Finding Description
`parseUniversalTxEvent` reads a raw 32-byte "dynamic data offset" word directly out of the gateway log's `Data` field with no upper-bound validation: [1](#0-0) 

That `dataOffset` (a `uint64` derived from a full 32-byte word, so it can be as large as `2^64-1`) flows into `decodePayload`, which only rejects it if it is *too small*, not if it is out of range at the top end: [2](#0-1) 

`readDynamicBytes` then performs the bounds check using `uint64` addition that can wrap around: [3](#0-2) 

If `absOff` is chosen close to `math.MaxUint64`, `absOff+32` wraps to a small value, so the guard `absOff+32 > uint64(len(data))` can evaluate `false` even though `absOff` itself is enormous. Execution then reaches `data[absOff : absOff+32]` with `absOff` far larger than `len(data)`, which is a slice-bounds violation and triggers a Go runtime panic (`slice bounds out of range`) rather than the intended `"", false` graceful rejection. This mirrors the reported class exactly: index arithmetic incremented/derived from external input without checked arithmetic before being used to address a buffer.

### Impact Explanation
If this panic occurs inside the observer/watcher goroutine that ingests external-chain gateway logs without a `recover()` wrapping each log's parsing, an unprivileged actor who can get a qualifying event emitted on the *external* (non-Push) chain — e.g., by calling the real Gateway contract's public send-funds/execute function with a crafted "payload" ABI offset field, which is ordinary user-reachable input, not privileged — can crash every honest validator's `universalClient` process that observes that chain. This is a node-level (not merely network-level) denial of service triggered purely by an ordinary user transaction/payload, which falls in scope per the "denial of service only when it is not network-level and is reachable without privileged control" allowance.

### Likelihood Explanation
Reachability depends on two facts I could not fully confirm in this session due to tool-call limits:
1. Whether log ingestion filters strictly to the configured `GatewayAddress`/event signature before calling `ParseEvent` (found references to `FilterLogs`/`GatewayAddress` in `universalClient/chains/evm/event_listener.go` and `client.go` but did not get to read their bodies to confirm filtering guarantees the offset field is bounded by an honest Gateway ABI encoder rather than arbitrary attacker-chosen bytes).
2. Whether the event-processing loop wraps per-log parsing in `recover()`, which would downgrade this from a crash to a logged/skipped error.

Because the Gateway contract itself likely ABI-encodes the payload offset correctly for legitimate calls, the attacker would need a way to make the *observed* event contain an out-of-range offset word — this is plausible if the Gateway's `payload`/`signatureData` are attacker-supplied bytes forwarded verbatim into the emitted event without the Gateway contract itself constraining the offset (worth checking the Gateway Solidity source, which is out of this repo's Go/Rust scope and wasn't available in the index).

### Recommendation
- In `decodePayload`/`decodeSignatureData`/`readDynamicBytes`, use `checked` arithmetic (e.g., compare `absOff <= uint64(len(data))-32` instead of `absOff+32 > len(data)`, or use `math/bits.Add64` and check the carry) so that offsets near `math.MaxUint64` cannot bypass the bounds check via wraparound.
- Wrap per-event parsing (`ParseEvent`/`parseUniversalTxEvent`) in a `recover()` so a single malformed/malicious log cannot crash the whole observer process, regardless of parsing bugs.
- Add an explicit sanity cap on `dataOffset`/`offset` values (e.g., reject anything larger than a small multiple of `len(log.Data)`) before doing any arithmetic on them.

### Proof of Concept
Conceptual: `readDynamicBytes(data, absOff)` with `absOff = math.MaxUint64 - 16` and `len(data) = 500`:
- `absOff+32` wraps to `15` (uint64 overflow), so `15 > 500` is `false` — check passes.
- Code proceeds to evaluate `data[absOff:absOff+32]` i.e. `data[18446744073709551599:15]`, which Go's runtime slice-bounds check rejects with a panic (`slice bounds out of range`), since `absOff` itself vastly exceeds `len(data)` and `low > high`.
- If this occurs while decoding a live external-chain gateway log inside an unrecovered goroutine, the `universalClient` process crashes.

I was not able to verify within the available iterations whether (a) the log-filtering path guarantees only genuine Gateway-contract events reach `ParseEvent`, and (b) whether a `recover()` exists around the parsing call site — both are necessary to confirm this reaches the "reachable without privileged control" and "not network-level DoS" bar with certainty. A Devin session with full repository access (to read `universalClient/chains/evm/event_listener.go` and `client.go` in full, plus the on-chain Gateway Solidity contract if available) would be needed to close that gap.

### Citations

**File:** universalClient/chains/evm/event_parser.go (L169-174)
```go
	// Parse common static fields: token (Word 0), amount (Word 1)
	payload.Token = ethcommon.BytesToAddress(log.Data[0*32+12 : 0*32+32]).Hex()
	payload.Amount = new(big.Int).SetBytes(log.Data[1*32 : 2*32]).String()

	dataOffset := new(big.Int).SetBytes(log.Data[2*32 : 3*32]).Uint64()
	parseUniversalTx(event, log, dataOffset, &payload, logger)
```

**File:** universalClient/chains/evm/event_parser.go (L177-189)
```go
// readDynamicBytes decodes ABI-encoded dynamic bytes at the given absolute offset in data.
func readDynamicBytes(data []byte, absOff uint64) (string, bool) {
	if absOff+32 > uint64(len(data)) {
		return "", false
	}
	byteLen := new(big.Int).SetBytes(data[absOff : absOff+32]).Uint64()
	dataStart := absOff + 32
	dataEnd := dataStart + byteLen
	if dataEnd > uint64(len(data)) {
		return "", false
	}
	return "0x" + hex.EncodeToString(data[dataStart:dataEnd]), true
}
```

**File:** universalClient/chains/evm/event_parser.go (L201-212)
```go
// decodePayload reads the raw payload bytes at the given offset and stores the hex string.
// The core validator will decode the universal payload from these raw bytes.
func decodePayload(data []byte, dataOffset uint64, payload *common.UniversalTx, logger zerolog.Logger) {
	if dataOffset < uint64(32*5) {
		return
	}
	hexStr, ok := readDynamicBytes(data, dataOffset)
	if !ok {
		return
	}
	payload.RawPayload = hexStr
}
```
