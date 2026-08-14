This confirms that unlike `ccipsolana` and `ccipsui`, the EVM message-hasher path (`ccipevm`) has no explicit length check on `msg.Receiver` before conversion — it relies solely on `common.BytesToAddress`'s silent-truncation semantics.

### Title
Silent truncation of malformed-length CCIP message `Receiver` bytes into an EVM address instead of reverting - (File: `core/capabilities/ccip/ccipevm/msghasher.go`)

### Summary
### Finding Description
The CCIP EVM message hasher converts the CCIP message's `Receiver` field (an arbitrary-length `cciptypes.UnknownAddress`/`[]byte`) into a 20-byte EVM address using `common.BytesToAddress(msg.Receiver)` [1](#0-0) , and the same unchecked conversion is used again when building the execution message via `CCIPMsgToAny2EVMMessage` [2](#0-1) . `common.BytesToAddress` (from go-ethereum) does not validate input length: for inputs longer than 20 bytes it silently keeps only the rightmost 20 bytes, and for inputs shorter than 20 bytes it left-pads with zero bytes — exactly the "silent truncation" pattern described in the ENS `proveAndClaim`/`hexToAddress` finding, where a malformed-length address is silently coerced into a valid-looking but different address instead of causing a rejection.

By contrast, the equivalent codecs for other chain families explicitly guard against this: `ccipsolana.AddressCodec.AddressBytesToString` rejects any address whose length isn't exactly `solana.PublicKeyLength` [3](#0-2) , `ccipsolana`'s message hasher explicitly checks `len(msg.Receiver)` and `len(ta.DestTokenAddress)` before use [4](#0-3) , and `ccipaptos`/`ccipsui` reject any address longer than 32 bytes in `addressBytesToBytes32` [5](#0-4) . The EVM path is the outlier lacking such a length guard before the truncating conversion.

### Impact Explanation
`msg.Receiver` originates from the CCIP message that any unprivileged user constructs when sending a cross-chain message (e.g., to a `Client.EVM2AnyMessage`/equivalent on a source chain, or via a non-EVM source chain's message format where the receiver field is not naturally 20 bytes). If a user supplies a `Receiver` value that is not exactly 20 bytes, the node computing/verifying the message hash (used in OCR consensus for the CCIP Commit/Execute reports) will silently coerce it into a different 20-byte address rather than reject the malformed message. This mirrors the ENS bug's impact category ("misreporting/data tampering") — the on-chain execution report could carry a `receiver`/`destTokenAddress` different from what the user actually intended, and because the coercion is deterministic and attacker-computable, an attacker could deliberately craft an oversized/undersized `Receiver` to target a specific truncated/padded address of their choosing.

### Likelihood Explanation
Low-to-moderate: like the original finding, this requires a user (or a malicious message originator, which is not a "malicious node/operator" but an ordinary cross-chain message sender) to deliberately submit a malformed-length receiver address, which is an edge case not expected in normal SDK usage, and it is unclear whether earlier validation layers (e.g., on-chain OnRamp contracts or upstream message readers) already reject non-20-byte receivers for EVM destinations before this code is reached — this could not be fully confirmed within the indexed code.

### Recommendation
Add an explicit length check in `MessageHasherV1.Hash` and `CCIPMsgToAny2EVMMessage` (mirroring the pattern already used in `ccipsolana`/`ccipsui`/`ccipaptos`) before calling `common.BytesToAddress`, rejecting any `msg.Receiver` or `DestTokenAddress` whose length is not exactly 20 bytes, e.g.:
```go
if len(msg.Receiver) != common.AddressLength {
    return [32]byte{}, fmt.Errorf("invalid receiver length: %d", len(msg.Receiver))
}
```

### Proof of Concept
Not independently reproducible from static code review alone (no test harness available in this pass to construct and hash a CCIP message with a non-20-byte `Receiver`). Conceptually: construct a `cciptypes.Message` with `Receiver` set to 22 arbitrary bytes and call `ccipevm.MessageHasherV1.Hash` — per `common.BytesToAddress` semantics, the resulting address in the hash preimage will be the last 20 bytes of the 22-byte input, silently discarding the first 2 bytes, with no error raised, analogous to the `arbitrarybytes`/`newOwner` PoC in the original ENS report.

**Note:** Due to index size limits, I could not verify whether an earlier validation layer (e.g., the EVM OnRamp Solidity contract, or an upstream CCIP reader) already enforces a strict 20-byte receiver length before this Go code path is reached, which would materially affect the likelihood assessment. Confirming this would require starting a full Devin session with complete repository access.

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

**File:** core/capabilities/ccip/ccipevm/helpers.go (L66-79)
```go
	return offramp.InternalAny2EVMRampMessage{
		Header: offramp.InternalRampMessageHeader{
			MessageId:           msg.Header.MessageID,
			SourceChainSelector: uint64(msg.Header.SourceChainSelector),
			DestChainSelector:   uint64(msg.Header.DestChainSelector),
			SequenceNumber:      uint64(msg.Header.SequenceNumber),
			Nonce:               msg.Header.Nonce,
		},
		Sender:       common.LeftPadBytes(msg.Sender, 32),
		Data:         msg.Data,
		Receiver:     common.BytesToAddress(msg.Receiver),
		GasLimit:     gasLimit,
		TokenAmounts: tokenAmounts,
	}, nil
```

**File:** core/capabilities/ccip/ccipsolana/addresscodec.go (L12-17)
```go
func (a AddressCodec) AddressBytesToString(addr []byte) (string, error) {
	if len(addr) != solana.PublicKeyLength {
		return "", fmt.Errorf("invalid SVM address length, expected %v, got %d", solana.PublicKeyLength, len(addr))
	}
	return solana.PublicKeyFromBytes(addr).String(), nil
}
```

**File:** core/capabilities/ccip/ccipsolana/msghasher.go (L52-54)
```go
	if solana.PublicKeyLength != len(msg.Receiver) {
		return [32]byte{}, fmt.Errorf("invalid receiver length: %d", len(msg.Receiver))
	}
```

**File:** core/capabilities/ccip/ccipaptos/addresscodec.go (L62-70)
```go
func addressBytesToBytes32(addr []byte) ([32]byte, error) {
	if len(addr) > 32 {
		return [32]byte{}, fmt.Errorf("invalid Aptos address length, expected 32, got %d", len(addr))
	}
	var result [32]byte
	// Left pad by copying to the end of the 32 byte array
	copy(result[32-len(addr):], addr)
	return result, nil
}
```
