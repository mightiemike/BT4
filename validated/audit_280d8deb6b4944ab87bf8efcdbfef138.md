### Title
Unchecked `uint64` offset arithmetic in EVM inbound event decoding can panic the universal client's log processing — (File: `universalClient/chains/evm/event_parser.go`)

### Summary
The reported HyVM bug is a class of "unchecked pointer/offset addition" — `add()` on an attacker-controlled offset with no overflow check, letting the offset wrap and land the code somewhere unintended. The closest native analog in Push Chain is in `readDynamicBytes`, `decodePayload`, and `decodeSignatureData` in [1](#0-0) , which compute ABI-style dynamic-bytes offsets from raw source-chain event log data using unchecked `uint64` addition.

### Finding Description
`parseUniversalTxEvent` reads `dataOffset` directly from event log bytes with no range restriction beyond `Uint64()` truncation: [2](#0-1) . That value flows into `decodePayload`/`readDynamicBytes`, and a second offset (`signatureData`) flows through `decodeSignatureData`, which performs the same unguarded arithmetic: [3](#0-2) .

Inside `readDynamicBytes`:
```go
if absOff+32 > uint64(len(data)) {
    return "", false
}
byteLen := new(big.Int).SetBytes(data[absOff : absOff+32]).Uint64()
``` [4](#0-3) 

`absOff` is a `uint64` derived from a 32-byte word truncated via `big.Int.Uint64()` (mod 2^64). If `absOff` is close to `math.MaxUint64`, `absOff+32` wraps around to a small value in **both** the bounds-check expression and the slice upper-bound expression, but the slice's **lower** bound (`absOff` itself) remains the original huge value. This produces a slice expression of the form `data[huge_value : small_wrapped_value]`, i.e. low > high, which is a Go runtime panic ("slice bounds out of range"), not silent data corruption — this is the closest available manifestation of the "unchecked add on an attacker-controlled offset" bug class in a Go codebase, versus memory-slot overwrite in a Huff/EVM interpreter.

This differs materially from the HyVM report in mechanism (Go runtime panic vs. EVM memory-slot corruption), and I could not verify two things needed to fully confirm exploitability and blast radius within the review time available:
- Whether the gateway/source-chain contract that emits these events re-encodes event data from decoded Solidity values (which would normalize/clobber any malicious raw offset word before it ever reaches the log), or instead echoes attacker-supplied raw bytes verbatim into the log. I found no Solidity gateway/event source in this repository to confirm the encoding path — the event ABI layout comment in `event_parser.go` documents the expected fields but not the emitting contract's implementation [5](#0-4) .
- Whether the goroutine that calls `ParseEvent`/`parseUniversalTxEvent` during event indexing wraps this call in a `recover()`. I searched `universalClient/**` for `recover()` and found none in the event-processing/indexer paths, only in unrelated signer test files, but I did not locate and fully trace every caller of `ParseEvent` to confirm the panic is unrecovered at the top level.

### Impact Explanation
If reachable and unrecovered, a panic here would crash the goroutine (or process, depending on Go's panic propagation and any outer `recover`) responsible for parsing inbound source-chain events feeding the universal-execution voting pipeline (inbound → ballot → finalization). This maps to the in-scope "denial of service ... reachable without privileged control" category, since it would be triggered by ordinary, unprivileged source-chain transaction/event data rather than a privileged validator or node action. It does not, by itself, corrupt UTX state, mint/burn funds, or bypass authorization — the impact is availability of the observer/indexer, not a fund-safety or authorization break, unlike the HyVM report's memory-overwrite scenario.

### Likelihood Explanation
Low-to-uncertain. The theoretical arithmetic flaw is real and directly analogous to `FIX_MEMOFFSET`'s missing overflow check, but I could not confirm within this pass that an unprivileged user can actually inject a wraparound-triggering offset value into `log.Data` for a real gateway event (this depends on the source-chain contract's Solidity encoding, which normally re-derives dynamic-bytes offsets deterministically and would neutralize a malicious raw offset before emission), nor could I confirm the panic escapes without recovery at a higher layer.

### Recommendation
- Add explicit overflow-safe bounds checks in `readDynamicBytes`, `decodePayload`, and `decodeSignatureData` (e.g., verify `absOff <= math.MaxUint64-32` before adding, or work in `big.Int`/`int` with prior range validation against `len(data)` before any addition) so a crafted offset cannot produce a low>high slice expression.
- Wrap event-log parsing (`ParseEvent` and its callers in the chain observer/indexer) in a `recover()` so malformed or adversarial log data cannot terminate the indexing goroutine.
- Confirm and document whether the source-chain gateway contract can ever emit attacker-influenced raw offset words in event data; if so, treat this as a confirmed unprivileged DoS trigger.

### Proof of Concept
Not independently confirmed end-to-end due to missing gateway/event-emission source code in this repository. The unit-level trigger for the Go-level panic is:
```go
data := make([]byte, 64)
absOff := uint64(0xFFFFFFFFFFFFFFF0) // near-max uint64, wraps on +32
readDynamicBytes(data, absOff) // slice data[absOff:wrapped_small_value] → panic: slice bounds out of range
```
Whether an external, unprivileged attacker can cause `absOff` to reach such a value inside real inbound event `log.Data` depends on the emitting contract's encoding behavior, which is outside this repository's indexed contents and should be verified with a full Devin session that can access the associated gateway contract source and trace the live indexer's error-handling/goroutine-recovery paths.

### Citations

**File:** universalClient/chains/evm/event_parser.go (L173-174)
```go
	dataOffset := new(big.Int).SetBytes(log.Data[2*32 : 3*32]).Uint64()
	parseUniversalTx(event, log, dataOffset, &payload, logger)
```

**File:** universalClient/chains/evm/event_parser.go (L177-225)
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

// readWord returns the i-th 32-byte word from data, or nil if out of bounds.
func readWord(data []byte, i int) []byte {
	start := i * 32
	end := start + 32
	if start < 0 || end > len(data) {
		return nil
	}
	return data[start:end]
}

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

// decodeSignatureData decodes the signature/verification data from a word that contains
// either a dynamic offset or fixed bytes32.
func decodeSignatureData(data []byte, w []byte, minOffset uint64) string {
	offset := new(big.Int).SetBytes(w).Uint64()
	if offset >= minOffset && offset < uint64(len(data)) {
		if hexStr, ok := readDynamicBytes(data, offset); ok {
			return hexStr
		}
	}
	// Fallback: treat as fixed bytes32
	return "0x" + hex.EncodeToString(w)
}
```

**File:** universalClient/chains/evm/event_parser.go (L242-253)
```go
/*
UniversalTx Event (V2 - upgraded chains):
  - sender (address, indexed)
  - recipient (address, indexed)
  - token (address)             — Word 0
  - amount (uint256)            — Word 1
  - payload (bytes)             — Word 2 (offset)
  - revertRecipient (address)   — Word 3
  - txType (TX_TYPE)            — Word 4
  - signatureData (bytes)       — Word 5 (offset)
  - fromCEA (bool)              — Word 6
*/
```
