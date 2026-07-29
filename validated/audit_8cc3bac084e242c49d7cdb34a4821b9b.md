## #Vulnerability found for this question.

### Title
Flat, size-independent gas for `verifyEd25519RawMessage` allows unbounded SHA-512 computational amplification via memory-reuse looping - (File: `precompiles/usigverifier/usigverifier.go`, `precompiles/usigverifier/query.go`, `precompiles/usigverifier/USigVerifier.sol`)

### Summary
`verifyEd25519RawMessage` accepts an arbitrary-length `message` argument but is metered with a fixed `VerifyEd25519RawMessageGas = 4000` regardless of message size [1](#0-0) . Because EVM `STATICCALL` reads its `argsOffset/argsSize` directly from already-expanded memory with no per-byte re-read cost, an attacker-deployed contract can load one large message into memory once (paying calldata gas for it) and then invoke the precompile in a bytecode loop referencing that same memory region hundreds/thousands of times, each iteration costing only the flat ~4000 execution gas plus a small warm-call overhead — independent of message length.

### Finding Description
`RequiredGas` returns a constant for `VerifyEd25519RawMessageMethod` [2](#0-1) , and `VerifyEd25519RawMessage` passes the full, unbounded `message` slice straight into `ed25519.Verify`, whose cost is dominated by SHA-512 hashing over the entire message with no length cap or validation on `message` (unlike `pubKey`/`signature`, which are length-checked) [3](#0-2) .

Normal Ethereum-style variable-length precompiles (e.g. MODEXP) or opcodes (e.g. KECCAK256) charge gas that scales with input size specifically to prevent this class of attack — hash/compute a large buffer once, then reference it repeatedly for near-zero marginal gas. This precompile does not follow that pattern.

An unprivileged attacker can:
1. Submit ordinary calldata to allocate a large `message` buffer (e.g. ~3–4 MB) into EVM memory once, paying normal calldata gas for it.
2. Execute a hand-crafted bytecode loop (deployable by any external account — no privilege required) issuing repeated `STATICCALL`s to `0xEC00000000000000000000000000000000000001` referencing the *same* memory offset/size for every iteration.
3. Each iteration costs only ~4000 (fixed `RequiredGas`) + ~100 (warm address access) gas, regardless of message size, while `ed25519.Verify` re-hashes the full multi-MB message every single call.

Given a representative ~30M block gas budget, the optimum split (message ≈ 3.6 MB, thousands of reuse calls) allows on the order of gigabytes of SHA-512 input to be processed within a single block's gas allowance — roughly three orders of magnitude more hashing work than a single maximal-calldata call to the same method would cost, and enormously more than the flat gas price implies.

### Impact Explanation
This breaks the invariant that block gas consumed should bound wall-clock execution time. Every honest node must re-execute (or re-validate) this transaction during block proposal, consensus, and later chain replay/sync. A crafted transaction that consumes gigabytes of SHA-512 work for one block's worth of gas materially inflates block execution wall-clock time relative to what other transactions of similar gas cost require, stalling/delaying block finalization and replay across all honest full nodes — a non-network-level DoS reachable purely through ordinary, unprivileged contract deployment and transaction submission (no malicious validator/UV/relayer/admin assumption needed).

### Likelihood Explanation
High. The attack requires only deploying an arbitrary bytecode contract and submitting one transaction — capabilities available to any unprivileged EVM user. No special permissions, precompile misconfiguration, or protocol race condition is needed; it is a direct consequence of the flat gas constant not scaling with `message` length combined with standard EVM memory-reuse semantics for repeated calls.

### Recommendation
Charge `VerifyEd25519RawMessageGas` (and ideally `VerifyEd25519Gas`) as a function of `len(message)` (e.g., a base cost plus a per-byte/per-hash-block surcharge reflecting SHA-512's actual block-processing cost), and/or enforce a maximum accepted `message` length in `VerifyEd25519RawMessage`/`VerifyEd25519` independent of calldata economics, similar to how Ethereum's MODEXP precompile scales gas with input size to prevent identical amplification attacks.

### Proof of Concept
1. Deploy a contract containing raw EVM bytecode (via inline assembly) that:
   - Writes a ~3–4 MB `message` buffer plus a valid 32-byte `pubKey` and 64-byte `signature` (validity of the signature is irrelevant — `ed25519.Verify` performs the same SHA-512 work whether it returns `true` or `false`) once into memory, pre-encoding one ABI call payload for `verifyEd25519RawMessage`.
   - Loops thousands of times issuing `staticcall(gas(), 0xEC...01, ptr, len, 0, 0)` referencing that same fixed memory region each iteration.
2. Measure the wall-clock time to execute this single transaction locally in a Go test harness that instantiates the precompile and calls `Run` in a loop with the same large `message`, and compare `elapsed_time / gas_used` against the ratio produced by ordinary transactions of similar gas cost (e.g., simple transfers or fixed-size precompile calls).
3. Observe that the ratio for the crafted transaction is orders of magnitude higher, demonstrating the absence of a size-scaled gas cost or message-length cap in `VerifyEd25519RawMessage` [4](#0-3) .

### Citations

**File:** precompiles/usigverifier/usigverifier.go (L17-22)
```go
	// VerifyEd25519Gas is the gas cost for verifying an Ed25519 signature.
	VerifyEd25519Gas uint64 = 4000
	// VerifyEd25519RawMessageGas matches VerifyEd25519Gas — same Ed25519
	// verification cost, only the message-prep step differs (no hex encoding).
	VerifyEd25519RawMessageGas uint64 = 4000
)
```

**File:** precompiles/usigverifier/usigverifier.go (L78-86)
```go
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
