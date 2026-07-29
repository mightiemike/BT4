## Analysis

Confirmed: `RequiredGas` charges a flat fee for both Ed25519 verification methods, regardless of message length. [1](#0-0) [2](#0-1) 

`VerifyEd25519RawMessage` accepts an arbitrary-length `message []byte` and runs `ed25519.Verify(pubKeyBytes, message, signature)`, whose cost (dominated by SHA-512 over the message) is linear in `len(message)`: [3](#0-2) 

This is in contrast to `VerifyEd25519`, whose message is a fixed `bytes32` digest — correctly priced flat since work is O(1) there.

The EVM's memory-expansion gas model does impose an upfront quadratic cost the first time a large calldata buffer is materialized in memory, but once that memory region is "paid for," a looping caller contract can issue repeated `CALL`s to the precompile reusing the *same* memory offset/length. Reusing already-expanded memory as `CALL` arguments does not incur additional expansion gas in the EVM gas-accounting model — only the flat `RequiredGas` (4000) plus base call/warm-address overhead is charged per call. This lets an attacker amortize the one-time memory cost across many verification calls, each doing full-length Ed25519/SHA-512 work for a fixed 4000 gas, producing a real mismatch between charged gas and CPU time consumed — the classic reason precompiles like `MODEXP`/`BN256`/`BLAKE2F` in mainline go-ethereum price gas as a function of input size/rounds rather than flat.

This fits the "non-network-level DoS reachable without privileged control" allowed-impact category: it requires only an ordinary unprivileged contract deployer/caller, no validator/relayer/admin privilege.

### Title
Flat gas pricing for variable-length input in `VerifyEd25519RawMessage` enables gas/CPU-time mismatch (execution-time DoS) - (File: precompiles/usigverifier/usigverifier.go, precompiles/usigverifier/query.go)

### Summary
`VerifyEd25519RawMessageGas` is a fixed 4000 gas regardless of the size of the attacker-supplied `message` argument, while the underlying `ed25519.Verify` call performs SHA-512 hashing work linear in message length. A looping caller contract can construct one large message buffer in memory (paying the EVM's one-time quadratic memory-expansion cost) and then repeatedly `CALL` the precompile referencing the same memory region, paying only the flat 4000 gas per call while the node performs O(len(message)) cryptographic work each time.

### Finding Description
`RequiredGas` in `usigverifier.go` returns `VerifyEd25519RawMessageGas` (4000) for any input regardless of the length of the `message` bytes parameter. `VerifyEd25519RawMessage` in `query.go` then calls `ed25519.Verify(pubKeyBytes, message, signature)` on the full message, whose cost scales with `len(message)` due to the SHA-512 hash computed internally by the Ed25519 verification algorithm. Because EVM memory-expansion gas is only charged when the active memory size grows, a contract that reuses the same memory offset/length across repeated `CALL`s to the precompile does not pay additional memory-expansion gas for subsequent calls — only the fixed `RequiredGas` plus ordinary call overhead. This breaks the assumption that gas charged per precompile invocation is proportional to the computational work performed.

### Impact Explanation
This allows a single unprivileged, gas-limit-bounded transaction to consume disproportionately more CPU time on hashing than its gas cost implies, compared to other opcodes/precompiles calibrated so that gas roughly tracks execution time. This is a resource/CPU-time DoS vector reachable purely through ordinary contract calls, not requiring any privileged actor.

### Likelihood Explanation
Reachable by any unprivileged actor able to deploy and invoke a simple looping contract that calls the `usigverifier` precompile at `0xEC00000000000000000000000000000000000001`; no special permissions, validator, TSS, or relayer role required.

### Recommendation
Price `VerifyEd25519RawMessage` (and any future arbitrary-length-input precompile methods) with a gas cost that scales with `len(message)`, e.g., `VerifyEd25519RawMessageGas + perByteGas * len(message)`, similar to how `MODEXP`/`BLAKE2F` scale gas with input size/rounds in upstream go-ethereum. Alternatively, cap the maximum accepted `message` length to bound worst-case per-call work.

### Proof of Concept
1. Deploy a contract `Looper` that:
   - Writes a large `message` (e.g., ~100–120 KB, sized to make the one-time EVM memory-expansion cost affordable within a target gas budget) into memory once.
   - In a loop, issues `STATICCALL`/`CALL` to `0xEC00000000000000000000000000000000000001` invoking `verifyEd25519RawMessage(pubKey, message, signature)` using the same memory offset/length each iteration.
2. Submit a transaction invoking `Looper` with a gas limit set so that `n * 4000` (plus base call overhead) fits under the limit, where `n` is large (e.g., thousands of iterations).
3. Measure wall-clock time spent inside `ed25519.Verify` across the `n` calls (e.g., via a Go benchmark instrumenting `Run`) versus the gas consumed, showing CPU time scaling with `n * len(message)` while gas scales only with `n * 4000`.

### Citations

**File:** precompiles/usigverifier/usigverifier.go (L15-22)
```go
const (
	USigVerifierPrecompileAddress = "0xEC00000000000000000000000000000000000001"
	// VerifyEd25519Gas is the gas cost for verifying an Ed25519 signature.
	VerifyEd25519Gas uint64 = 4000
	// VerifyEd25519RawMessageGas matches VerifyEd25519Gas — same Ed25519
	// verification cost, only the message-prep step differs (no hex encoding).
	VerifyEd25519RawMessageGas uint64 = 4000
)
```

**File:** precompiles/usigverifier/usigverifier.go (L78-85)
```go
	switch method.Name {
	case VerifyEd25519Method:
		return VerifyEd25519Gas
	case VerifyEd25519RawMessageMethod:
		return VerifyEd25519RawMessageGas
	default:
		return p.Precompile.RequiredGas(input, p.IsTransaction(method))
	}
```

**File:** precompiles/usigverifier/query.go (L61-93)
```go
func (p Precompile) VerifyEd25519RawMessage(
	method *abi.Method,
	args []interface{},
) ([]byte, error) {

	pubKey, ok := args[0].([]byte)
	if !ok {
		return nil, fmt.Errorf("invalid pubKey type")
	}

	message, ok := args[1].([]byte)
	if !ok {
		return nil, fmt.Errorf("invalid message type")
	}

	signature, ok := args[2].([]byte)
	if !ok {
		return nil, fmt.Errorf("invalid signature type")
	}

	pubKeyBytes, err := getSolanaPubKeyFromAddress(pubKey)
	if err != nil {
		return nil, fmt.Errorf("failed to parse pubKey: %w", err)
	}

	if len(pubKeyBytes) != ed25519.PublicKeySize || len(signature) != ed25519.SignatureSize {
		return nil, fmt.Errorf("invalid params")
	}

	ok = ed25519.Verify(pubKeyBytes, message, signature)

	return method.Outputs.Pack(ok)
}
```
