## Finding: Fixed-cost `usigverifier` precompile does not scale gas with `message` length

### Title
Flat 4000-gas charge for `verifyEd25519RawMessage` regardless of message size enables underpriced computational DoS - (File: `precompiles/usigverifier/usigverifier.go`)

### Summary
The `usigverifier` precompile charges a fixed `VerifyEd25519RawMessageGas = 4000` for `verifyEd25519RawMessage(bytes,bytes,bytes)` no matter how large the `message` argument is, while the underlying computation (`ed25519.Verify`, which internally computes SHA-512 over the full message) scales linearly with message length [1](#0-0) [2](#0-1) . This is the same bug class as the external Modexp report: a precompile's declared/charged gas cost is decoupled from the real cost of the operation it performs, and an attacker fully controls the size of the expensive input.

### Finding Description
`RequiredGas` returns a constant for both `verifyEd25519` and `verifyEd25519RawMessage`:
```go
VerifyEd25519Gas uint64 = 4000
VerifyEd25519RawMessageGas uint64 = 4000
...
case VerifyEd25519RawMessageMethod:
    return VerifyEd25519RawMessageGas
``` [3](#0-2) 

`verifyEd25519RawMessage` accepts `message` as arbitrary-length `bytes` — per the Solidity interface and README, "`message` may be any length, not just 32 bytes" [4](#0-3) [5](#0-4) . `VerifyEd25519RawMessage` passes the full `message` slice straight into `ed25519.Verify(pubKeyBytes, message, signature)` with no length cap [6](#0-5) . Ed25519 verification computes SHA-512 over the entire message, so the real CPU cost is proportional to `len(message)`, but the gas charged is a constant 4000 regardless of size.

A contract can amortize the one-time cost of building a large memory buffer (paid via quadratic EVM memory-expansion gas) across many subsequent precompile calls that reuse the same memory region as `message`. Each subsequent call to `verifyEd25519RawMessage` re-hashes the entire buffer but is charged only the flat 4000 gas plus ordinary CALL overhead — not a cost proportional to the buffer size. This lets an attacker force validators/full nodes to perform far more SHA-512/Ed25519 computation per unit of gas paid than other EVM primitives of comparable cost (e.g., the standard `sha256` precompile scales cost with input words), producing a computational DoS vector that is disproportionate to the gas spent.

### Impact Explanation
Any unprivileged address can deploy a contract that allocates a large calldata/memory buffer once and then loops calls into `0xEC00000000000000000000000000000000000001.verifyEd25519RawMessage(...)` against that buffer, paying a flat, size-independent 4000 gas per call while nodes perform hashing work proportional to the buffer size. This inflates node CPU/verification time relative to the gas actually billed, which is a non-network-level, unprivileged-triggerable denial-of-service vector reachable from ordinary contract execution — matching the "denial of service...reachable without privileged control" allowed impact category.

### Likelihood Explanation
Trivial to trigger: no special privileges, keys, or validator cooperation are required — just a contract calling a public, always-active precompile with attacker-chosen calldata size. The only friction is the one-time memory-expansion cost to build the reusable buffer, which is cheap relative to the number of underpriced re-hash calls that can follow.

### Recommendation
Charge gas for `verifyEd25519RawMessage` (and ideally review `verifyEd25519`, though its digest is fixed at 32 bytes so it is not affected) proportional to `len(message)`, e.g. a base cost plus a per-word/per-byte multiplier similar to the standard `sha256`/`ripemd160` precompiles, so the gas charged tracks the real SHA-512 hashing cost incurred by `ed25519.Verify`.

### Proof of Concept
1. Deploy a helper contract that builds a large `bytes memory buf` (e.g., 100 KB) once.
2. In a loop, call `USigVerifier_CONTRACT_V2.verifyEd25519RawMessage(pubKey, buf, signature)` N times, reusing `buf` as calldata each time (any dummy/invalid signature is fine since gas is charged before/regardless of the boolean result).
3. Each call is charged only 4000 gas (plus ordinary CALL overhead) via `RequiredGas`/`VerifyEd25519RawMessageGas`, while each call internally re-hashes the full 100 KB buffer via `ed25519.Verify`, so overall gas paid does not scale with the total bytes hashed across the loop, unlike comparable fixed-cost precompiles whose gas does scale with input size.

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

**File:** precompiles/usigverifier/usigverifier.go (L66-86)
```go
func (p Precompile) RequiredGas(input []byte) uint64 {
	// NOTE: This check avoid panicking when trying to decode the method ID
	if len(input) < 4 {
		return 0
	}

	methodID := input[:4]
	method, err := p.ABI.MethodById(methodID)
	if err != nil {
		return 0
	}

	switch method.Name {
	case VerifyEd25519Method:
		return VerifyEd25519Gas
	case VerifyEd25519RawMessageMethod:
		return VerifyEd25519RawMessageGas
	default:
		return p.Precompile.RequiredGas(input, p.IsTransaction(method))
	}
}
```

**File:** precompiles/usigverifier/query.go (L61-92)
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
```

**File:** precompiles/usigverifier/USigVerifier.sol (L24-33)
```text
    /// @notice Verifies an Ed25519 signature over raw message bytes.
    /// @dev Standard Ed25519 verification: signature is checked against the raw
    ///      bytes of `message`. Use this when your off-chain signer uses the
    ///      conventional `ed25519.Sign(privKey, rawBytes)` API (the default in
    ///      every Solana SDK / nacl library).
    /// @param pubKey 32-byte Ed25519 public key.
    /// @param message Raw message bytes that were signed (any length).
    /// @param signature 64-byte Ed25519 signature.
    /// @return isValid True iff signature is valid for (pubKey, message).
    function verifyEd25519RawMessage(bytes calldata pubKey, bytes calldata message, bytes calldata signature) external view returns (bool);
```

**File:** precompiles/usigverifier/README.md (L64-72)
```markdown
### `verifyEd25519RawMessage` — raw-bytes convention (standard)

Standard Ed25519 verification — signature is checked against the raw `message` bytes:

```go
ok = ed25519.Verify(pubKeyBytes, message, signature)
```

Use this when your signer uses `ed25519.Sign(privKey, rawBytes)` (default in every Solana SDK / nacl library). `message` may be any length, not just 32 bytes.
```
