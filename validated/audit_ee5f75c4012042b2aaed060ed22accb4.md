### Title
Unvalidated Receiver Length Silently Corrupted via `common.BytesToAddress` in CCIP EVM Message Hasher - ([File: core/capabilities/ccip/ccipevm/msghasher.go])

### Summary
The `MessageHasherV1.Hash` function used by Chainlink's CCIP OCR plugins to reconstruct the same message hash that on-chain OnRamp/OffRamp contracts compute passes `msg.Receiver` directly into `common.BytesToAddress()` without ever validating that the byte slice is exactly 20 bytes long, unlike every other chain-family codec in the same package tree (Solana, Aptos, Sui) which explicitly check length and return errors on mismatch.

### Finding Description
`MessageHasherV1.Hash` builds the `Any2EVMMessageHash` fixed-size fields preimage by calling: [1](#0-0) 

`common.BytesToAddress` (go-ethereum) does not error on malformed input: it truncates the leftmost bytes when the slice is longer than 20 bytes and left-pads with zero bytes when the slice is shorter. This is exactly the "silent burn" root cause pattern described in the external report — a bytes-to-address conversion path that succeeds and silently mutates the value instead of rejecting non-20-byte payloads.

This stands in sharp contrast to the other chain-family hashers in the same `core/capabilities/ccip` tree, which strictly validate receiver/address lengths before hashing: [2](#0-1) [3](#0-2) 

The EVM `AddressCodec.AddressBytesToString` (used elsewhere for logging/display, not in the hasher) also has no length validation and a comment acknowledging the gap: [4](#0-3) [5](#0-4) 
(note the test that would have caught this is explicitly commented out).

`msg.Receiver` (`cciptypes.UnknownAddress`) originates from off-chain-decoded reports/messages fed into the OCR3 commit/execute plugins running inside the node. If a `Receiver` value with a length other than 20 bytes were ever produced by an upstream decode step (malformed source-chain message, buggy `ExecutePluginCodec`, or a source chain family bug), this hasher would not reject it — it would silently compute a hash for a *different, wrong* EVM address rather than failing loudly.

### Impact Explanation
Because this hashing function participates directly in OCR3 report/observation construction for CCIP (the value is used to build the `Any2EVMMessageHash`, which must match the value verified on-chain), silent mangling of the receiver bytes is a misreporting/data-tampering-class defect rather than a purely cosmetic bug:
- If the hash computed here is used in the node's observation, consensus, or off-chain validation of CCIP reports and does not match validation logic elsewhere, it can cause the node to compute a wrong hash for a legitimate message (denial of correct execution for the intended EVM receiver).
- If some upstream decode path is not perfectly matched with what the on-chain OnRamp accepts, this local silent-truncation/padding behavior means the node has no defense-in-depth check to loudly flag/reject an obviously malformed CCIP message before hashing it — every other chain-family hasher (Solana, Aptos-related tests) treats this as a hard error, indicating this is a known-important invariant that was omitted specifically for the EVM path.

This does not itself constitute direct fund loss inside the Go codebase (there is no wallet debit here), so it is best framed as a data-integrity/misreporting risk in the CCIP OCR message-hashing trust boundary, contingent on it being reachable with attacker/user-influenced malformed receiver bytes.

### Likelihood Explanation
Reachability requires an `Receiver` byte slice of non-20-byte length to reach `MessageHasherV1.Hash`. Whether this is actually reachable end-to-end (i.e., whether upstream CCIP message decoding always guarantees 20-byte EVM receivers before invoking this hasher) could not be fully confirmed from the indexed code alone — the guarantee may exist elsewhere in the `chainlink-ccip` reporting pipeline (an external dependency not fully visible here). The presence of strict length checks in the sibling Solana/Aptos/Sui hashers, and the explicitly disabled test in `ccipevm/addresscodec_test.go`, indicate the omission is deliberate-but-unaddressed technical debt rather than a hardened, provably-unreachable path.

### Recommendation
Add an explicit length check (`len(msg.Receiver) != 20`) at the top of `MessageHasherV1.Hash` before calling `common.BytesToAddress`, returning an error for malformed receivers — mirroring the pattern already used in `ccipsolana/msghasher.go` (`if solana.PublicKeyLength != len(msg.Receiver) { return error }`). Re-enable and extend the commented-out `TestInvalidAddressBytesToString` test in `ccipevm/addresscodec_test.go`.

### Proof of Concept
Not independently exploitable from the indexed Go code alone (no clear evidence of a reachable path with attacker-controlled malformed `Receiver` bytes was found in this repo slice); the analog is illustrated by direct code inspection:
1. Construct a `cciptypes.Message` with `Receiver` set to a byte slice of length ≠ 20 (e.g., 4 or 32 bytes).
2. Call `MessageHasherV1.Hash(ctx, msg)`.
3. `common.BytesToAddress(msg.Receiver)` at [6](#0-5)  silently returns a truncated/padded `common.Address` instead of an error, and the function proceeds to compute and return a hash for the wrong receiver with no error signal — contrast with the Solana hasher's explicit rejection at [2](#0-1) .

### Citations

**File:** core/capabilities/ccip/ccipevm/msghasher.go (L193-200)
```go
	fixedSizeFieldsEncoded, err := h.abiEncode(
		"encodeFixedSizeFieldsHashPreimage",
		msg.Header.MessageID,
		common.BytesToAddress(msg.Receiver),
		uint64(msg.Header.SequenceNumber),
		gasLimit,
		msg.Header.Nonce,
	)
```

**File:** core/capabilities/ccip/ccipsolana/msghasher.go (L52-54)
```go
	if solana.PublicKeyLength != len(msg.Receiver) {
		return [32]byte{}, fmt.Errorf("invalid receiver length: %d", len(msg.Receiver))
	}
```

**File:** core/capabilities/ccip/ccipaptos/addresscodec.go (L35-41)
```go
func addressBytesToString(addr []byte) (string, error) {
	if len(addr) < 1 || len(addr) > 32 {
		return "", fmt.Errorf("invalid Aptos address length (%d)", len(addr))
	}

	return fmt.Sprintf("0x%064x", addr), nil
}
```

**File:** core/capabilities/ccip/ccipevm/addresscodec.go (L12-14)
```go
func (a AddressCodec) AddressBytesToString(addr []byte) (string, error) {
	return common.BytesToAddress(addr).Hex(), nil
}
```

**File:** core/capabilities/ccip/ccipevm/addresscodec_test.go (L28-34)
```go
// we allow various sizes since some contracts store the 20-byte address as 32-byte
// func TestInvalidAddressBytesToString(t *testing.T) {
// 	addressCodec := AddressCodec{}
// 	addr := []byte{0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12}
// 	_, err := addressCodec.AddressBytesToString(addr)
// 	require.Error(t, err)
// }
```
