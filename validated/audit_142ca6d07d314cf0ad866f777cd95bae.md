### Title
Missing canonical/low-S check before ECDSA public key recovery enables signature malleability - ([File: core/utils/eth_signatures.go])

### Summary
`GetSignersEthAddress` in `core/utils/eth_signatures.go` recovers the signer's address directly via `crypto.SigToPub` (which wraps the malleable `Ecrecover`) without first validating that the `s` component is in the canonical lower half of the curve order (EIP-2) or otherwise rejecting non-canonical signatures. This is the exact bug class described in the external report (`Ecrecover`/`SigToPub` used for authentication without a preceding `VerifySignature`/canonical-form check), and this helper is the shared low-level primitive used by several unprivileged, externally-reachable authentication paths in the gateway stack.

### Finding Description
`GetSignersEthAddress` only validates signature length and the `v` recovery byte before calling `crypto.SigToPub`: [1](#0-0) 

No check ensures `s <= secp256k1n/2` (the canonical form) or otherwise rejects the malleable counterpart `(r, n-s, 1-v)`, which recovers to the exact same address. Any party in possession of one valid `(r, s, v)` signature can trivially compute a second, equally-valid signature `(r, n-s, 1-v)` for the identical signed message.

This primitive is reused as the trust anchor for several externally-reachable, unprivileged-user-facing verification paths:
- `gw_common.ExtractSigner` (gateway) delegates straight to it: [2](#0-1) 
- Gateway `api.Message.ExtractSigner`, used by `Message.Validate()` to authenticate every inbound gateway JSON-RPC message from external users/DON nodes: [3](#0-2) [4](#0-3) 
- Gateway node handshake authentication (`UnpackSignedAuthHeader`) and challenge-response finalization (`FinalizeHandshake`), which gate WebSocket connection establishment: [5](#0-4) [6](#0-5) 
- S4 secret-storage envelope signature verification, `Envelope.GetSignerAddress`, used to authorize writes to a user's storage slot: [7](#0-6) 
- JWT signature verification (`SigningMethodEth.Verify`) built on the same helper: [8](#0-7) 

A separate, independent implementation of the same pattern also exists in `vaulttypes.ValidateSignatures`, which likewise calls `crypto.SigToPub` with no canonical-form check: [9](#0-8) 

### Impact Explanation
Because address recovery accepts both the canonical and the malleable form of any valid signature, any component downstream that relies on the *signature bytes themselves* being a unique, single-use token (rather than on the signed payload's own nonce/version fields) can be tricked into treating a re-derived malleable signature as a brand-new, distinct, still-valid credential for the same signer/content. This weakens any anti-replay or single-use assumption built on top of "signature equality" checks and is a violation of the safe-signing invariant expected by every one of the reachable call sites listed above (gateway message auth, node handshake, S4 envelope authorization, JWT auth). It does not by itself allow forging a signature for different content or a different signer, but it removes a defense-in-depth guarantee, and it exactly matches the vulnerability class validated by the Lombard remediation (using `VerifySignature`/canonical-form checks before recovering a public key from a signature).

### Likelihood Explanation
High feasibility to reproduce: signature malleability requires no compute-intensive attack — an attacker with any one valid signature (obtained from any legitimate message they signed, or observed on the wire) can locally derive the malleable twin in O(1) time (negate `s` modulo the curve order, flip `v`). All of the affected call sites (`Message.Validate`, gateway handshake, S4 envelope) accept externally supplied, unprivileged input over the network, making this trivially reachable by any user or node interacting with the gateway/S4 APIs.

### Recommendation
In `core/utils/eth_signatures.go:GetSignersEthAddress` (and the duplicated logic in `core/capabilities/vault/vaulttypes/types.go:ValidateSignatures`), reject non-canonical signatures before calling `crypto.SigToPub`/`crypto.Ecrecover`: verify `s` is less than or equal to `secp256k1n/2` (as done correctly in `deployment/environment/devenv/internal/kms/kms_client.go`'s `kmsToEthSig`, which already normalizes `s`), or use `crypto.VerifySignature` against the recovered/expected public key before trusting the recovered address. Apply the fix once in the shared helper and audit all its callers (gateway `Message`, handshake, S4 envelope, JWT, vault) to ensure none of them depend on signature-byte uniqueness as a replay-protection mechanism.

### Proof of Concept
1. Take any valid signed gateway `api.Message` (e.g. produced by `msg.Sign(privateKey)` as in `core/services/gateway/api/message_test.go`).
2. Decode the 65-byte signature into `(r, s, v)`.
3. Compute `s' = secp256k1n - s` and `v' = 1 - v` (or `28`/`27` flip), producing `(r, s', v')`.
4. Submit a new message with the same body/header but the malleable signature `(r, s', v')` to any endpoint that calls `Message.Validate()` / `ExtractSigner()` (e.g. `core/services/gateway/api/message.go`) — the signature is accepted, and `crypto.SigToPub` in `core/utils/eth_signatures.go` recovers the same signer address, confirming the second, distinct signature bypasses no malleability check.

### Citations

**File:** core/utils/eth_signatures.go (L14-37)
```go
func GetSignersEthAddress(msg []byte, sig []byte) (recoveredAddr common.Address, err error) {
	if len(sig) != 65 {
		return recoveredAddr, errors.New("invalid signature: signature length must be 65 bytes")
	}

	// Adjust the V component of the signature in case it uses 27 or 28 instead of 0 or 1
	if sig[64] == 27 || sig[64] == 28 {
		sig[64] -= 27
	}
	if sig[64] != 0 && sig[64] != 1 {
		return recoveredAddr, errors.New("invalid signature: invalid V component")
	}

	prefixedMsg := fmt.Sprintf("%s%d%s", EthSignedMessagePrefix, len(msg), msg)
	hash := crypto.Keccak256Hash([]byte(prefixedMsg))

	sigPublicKey, err := crypto.SigToPub(hash[:], sig)
	if err != nil {
		return recoveredAddr, err
	}

	recoveredAddr = crypto.PubkeyToAddress(*sigPublicKey)
	return recoveredAddr, nil
}
```

**File:** core/services/gateway/common/utils.go (L51-57)
```go
func ExtractSigner(signature []byte, data ...[]byte) (signerAddress []byte, err error) {
	addr, err := utils.GetSignersEthAddress(flatten(data...), signature)
	if err != nil {
		return nil, err
	}
	return addr.Bytes(), nil
}
```

**File:** core/services/gateway/api/message.go (L54-88)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/api/message.go (L124-134)
```go
func (m *Message) ExtractSigner() (signerAddress []byte, err error) {
	if m == nil {
		return nil, errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signatureBytes, err := hex.DecodeString(m.Signature)
	if err != nil {
		return nil, err
	}
	return gw_common.ExtractSigner(signatureBytes, rawData...)
}
```

**File:** core/services/gateway/network/handshake.go (L75-90)
```go
func UnpackSignedAuthHeader(data []byte) (elems *AuthHeaderElems, signer []byte, err error) {
	if len(data) != HandshakeAuthHeaderLen {
		return nil, nil, fmt.Errorf("auth header length is invalid (expected: %d, got: %d)", HandshakeAuthHeaderLen, len(data))
	}
	elems = &AuthHeaderElems{}
	offset := 0
	elems.Timestamp = common.BytesToUint32(data[offset : offset+HandshakeTimestampLen])
	offset += HandshakeTimestampLen
	elems.DonId = common.AlignedBytesToString(data[offset : offset+HandshakeDonIdLen])
	offset += HandshakeDonIdLen
	elems.GatewayId = common.AlignedBytesToString(data[offset : offset+HandshakeGatewayURLLen])
	offset += HandshakeGatewayURLLen
	signature := data[offset:]
	signer, err = common.ExtractSigner(signature, data[:len(data)-HandshakeSignatureLen])
	return
}
```

**File:** core/services/gateway/connectionmanager.go (L260-272)
```go
func (m *connectionManager) FinalizeHandshake(attemptId string, response []byte, conn *websocket.Conn) error {
	m.lggr.Debugw("FinalizeHandshake", "attemptId", attemptId)
	m.connAttemptsMu.Lock()
	attempt, ok := m.connAttempts[attemptId]
	delete(m.connAttempts, attemptId)
	m.connAttemptsMu.Unlock()
	if !ok {
		return network.ErrChallengeAttemptNotFound
	}
	signer, err := common.ExtractSigner(response, network.PackChallenge(&attempt.challenge))
	if err != nil || attempt.nodeAddress != "0x"+hex.EncodeToString(signer) {
		return network.ErrChallengeInvalidSignature
	}
```

**File:** core/services/s4/envelope.go (L49-59)
```go
// GetSignerAddress verifies the signature and returns the signing address.
func (e Envelope) GetSignerAddress(signature []byte) (address common.Address, err error) {
	if len(e.Address) != common.AddressLength {
		return common.Address{}, fmt.Errorf("invalid address length: %d", len(e.Address))
	}
	js, err := e.ToJson()
	if err != nil {
		return common.Address{}, err
	}
	return utils.GetSignersEthAddress(js, signature)
}
```

**File:** core/utils/jwt.go (L113-131)
```go
// Verify verifies the given signature for the given signing string using the given public key
// key is expected to be a gethcommon.Address
func (m *SigningMethodEth) Verify(signingString string, signature []byte, key any) error {
	var ethAddr gethcommon.Address
	switch k := key.(type) {
	case gethcommon.Address:
		ethAddr = k
	default:
		return jwt.ErrInvalidKeyType
	}
	recoveredAddr, err := GetSignersEthAddress([]byte(signingString), signature)
	if err != nil {
		return err
	}
	if !bytes.Equal(recoveredAddr.Bytes(), ethAddr.Bytes()) {
		return jwt.ErrSignatureInvalid
	}
	return nil
}
```

**File:** core/capabilities/vault/vaulttypes/types.go (L198-204)
```go
	validSigners := map[common.Address]bool{}
	for _, s := range resp.Signatures {
		signerPubkey, err := crypto.SigToPub(fullHash, s)
		if err != nil {
			return fmt.Errorf("invalid signature: %w", err)
		}
		signerAddr := crypto.PubkeyToAddress(*signerPubkey)
```
