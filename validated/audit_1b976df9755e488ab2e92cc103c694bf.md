I found a legitimate analog: the Chainlink Functions S4 secrets storage signature scheme (`core/services/s4/envelope.go`, used by `core/services/functions/connector_handler.go`) mirrors the exact flaw described in the report — a signature that authenticates a user action without binding it to a specific execution/network context (DON), enabling replay of a captured signed request across a different context that shares the same address/slot/version namespace.

### Title
Cross-DON Replay of S4 `secrets_set` Signatures Due to Missing DON/Chain Binding in `Envelope` Signing - (File: core/services/s4/envelope.go)

### Summary
The Chainlink Functions "S4" secrets-storage feature lets an unprivileged external user store an encrypted secret at a DON-hosted node by submitting a `secrets_set` gateway request signed with the user's own Ethereum key. The signature is computed over an `Envelope` (`address`, `slotid`, `payload`, `version`, `expiration`) that contains **no DON identifier, gateway/chain identifier, or domain separator**. Any signed envelope that is valid for one DON/gateway is therefore also valid for every other DON/gateway that runs the same S4/Functions handler code, exactly analogous to the reported cross-chain replay issue where a signature lacked `chainId` binding.

### Finding Description
`Envelope.Sign` / `Envelope.GetSignerAddress` in [1](#0-0)  compute/verify a signature over a JSON serialization containing only `address, slotid, payload, version, expiration` — there is no DON ID, chain ID, or gateway-specific salt in the signed payload, per the type definition [2](#0-1) .

This envelope/signature is exactly what is accepted server-side in the Functions gateway connector handler when a user submits a `secrets_set` request: `handleSecretsSet` builds an `s4.Key`/`s4.Record` from the untrusted request body and calls `h.storage.Put(ctx, &key, &record, request.Signature)` [3](#0-2) . The only checks in `HandleGatewayMessage` before reaching `handleSecretsSet` are an onchain allowlist check and a subscription-balance check keyed on `fromAddr`/`body.Sender` [4](#0-3)  — neither of which binds the request to the specific DON/gateway that originally received it.

Signature verification itself, in `storage.Put`, only checks that the recovered signer equals `key.Address`: [5](#0-4) . It performs no validation that the envelope was intended for *this* DON/gateway/network instance. The underlying recovery primitive, `GetSignersEthAddress` in `core/utils/eth_signatures.go`, likewise has no domain separation — it simply Keccak256-hashes an EIP-191-prefixed message with no chain/DON context [6](#0-5) .

The command-line reference client (`core/scripts/gateway/client/send_request.go`) confirms the exact fields an attacker would observe/capture in a legitimate request: `address, slotid, payload, version, expiration` plus `signature`, with no DON-binding field included in what's signed [7](#0-6) .

### Impact Explanation
An unprivileged attacker who observes a legitimate user's `secrets_set` request (e.g., via network capture, a malicious/compromised gateway, or by being a node forwarding traffic) can replay the exact same signed envelope against any other DON/gateway instance that independently runs the same Functions/S4 handler code and shares the same `(address, slotid, version)` key namespace. Since `slotid`/`version`/`expiration` are the only anti-replay-adjacent fields and they are user-chosen (not DON-scoped), and `Version` acts as an update guard only within a single storage instance (`ErrVersionTooLow`), the replay lands as a legitimate, signature-valid `Put` on a different DON. This can be used to plant or overwrite secret payloads under a victim's address on a DON the victim never intended to interact with, or to desynchronize secret state that downstream Functions computations rely on for a given user — a data-integrity/authorization-boundary violation reachable by any external, unprivileged party who can observe or intercept one signed request.

### Likelihood Explanation
Moderate-to-high: exploitation only requires observing one valid signed `secrets_set` payload (attacker doesn't need the private key) and forwarding it, unmodified, to a different DON's gateway that also runs the Functions S4 handler with the same subscription/allowlist config allowing the address. No fork or malicious-node capability is strictly required — a normal network observer/relay (unprivileged) suffices, consistent with the "malicious API/RPC client" attacker profile.

### Recommendation
Bind the S4 `Envelope` signature to the specific execution context by including a DON identifier (or equivalently, a gateway/chain-scoped domain separator) in the signed payload, mirroring the EIP-712 `chainId` fix recommended in the reference report. Concretely, add a `DonID` (or `ChainID`) field to `Envelope` in `core/services/s4/envelope.go`, include it in `ToJson()`, and have `handleSecretsSet` populate/verify it against the DON actually handling the request in `core/services/functions/connector_handler.go`, rejecting any envelope whose bound DON ID does not match.

### Proof of Concept
1. Attacker observes/intercepts a legitimate `secrets_set` JSON-RPC message sent by victim `V` to DON `A` (payload contains `address, slotid, payload, version, expiration, signature` per `functions.SecretsSetRequest` [8](#0-7) ).
2. Attacker resends the identical `payload`/`signature` bytes to DON `B`'s gateway (a different DON also running the Functions connector handler and allowlisting `V`'s address).
3. `handleSecretsSet` on DON `B` unmarshals the request and calls `storage.Put` with the same key/record/signature [3](#0-2) .
4. `storage.Put` recovers the signer from the envelope (address, slotid, payload, version, expiration only) and finds it matches `key.Address`, since no DON-binding was ever part of the signed data [5](#0-4) .
5. The secret is accepted and stored on DON `B` even though `V` never signed anything intended for DON `B`, confirming the cross-DON replay.

### Citations

**File:** core/services/s4/envelope.go (L19-25)
```go
type Envelope struct {
	Address    []byte `json:"address"`
	SlotID     uint   `json:"slotid"`
	Payload    []byte `json:"payload"`
	Version    uint64 `json:"version"`
	Expiration int64  `json:"expiration"`
}
```

**File:** core/services/s4/envelope.go (L37-59)
```go
// Sign calculates signature for the serialized envelope data.
func (e Envelope) Sign(privateKey *ecdsa.PrivateKey) (signature []byte, err error) {
	if len(e.Address) != common.AddressLength {
		return nil, fmt.Errorf("invalid address length: %d", len(e.Address))
	}
	js, err := e.ToJson()
	if err != nil {
		return nil, err
	}
	return utils.GenerateEthSignature(privateKey, js)
}

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

**File:** core/services/functions/connector_handler.go (L125-162)
```go
func (h *functionsConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		h.lggr.Errorw("failed to decode request", "id", gatewayID, "err", err)
		return nil
	}
	body := &msg.Body
	fromAddr := ethCommon.HexToAddress(body.Sender)
	if !h.allowlist.Allow(fromAddr) {
		h.lggr.Errorw("allowlist prevented the request from this address", "id", gatewayID, "address", fromAddr)
		return nil
	}
	if !h.rateLimiter.Allow(body.Sender) {
		h.lggr.Errorw("request rate-limited", "id", gatewayID, "address", fromAddr)
		return nil
	}
	h.lggr.Debugw("handling gateway request", "id", gatewayID, "method", body.Method)

	switch body.Method {
	case functions.MethodSecretsList:
		h.handleSecretsList(ctx, gatewayID, body, fromAddr)
	case functions.MethodSecretsSet:
		if balance, err := h.subscriptions.GetMaxUserBalance(fromAddr); err != nil || balance.Cmp(h.minimumBalance.ToInt()) < 0 {
			h.lggr.Errorw("user subscription has insufficient balance", "id", gatewayID, "address", fromAddr, "balance", balance, "minBalance", h.minimumBalance)
			response := functions.ResponseBase{
				Success:      false,
				ErrorMessage: "user subscription has insufficient balance",
			}
			h.sendResponseAndLog(ctx, gatewayID, body, response)
			return nil
		}
		h.handleSecretsSet(ctx, gatewayID, body, fromAddr)
	case functions.MethodHeartbeat:
		h.handleHeartbeat(ctx, gatewayID, body, fromAddr)
	default:
		h.lggr.Errorw("unsupported method", "id", gatewayID, "method", body.Method)
	}
	return nil
```

**File:** core/services/functions/connector_handler.go (L212-238)
```go
func (h *functionsConnectorHandler) handleSecretsSet(ctx context.Context, gatewayId string, body *api.MessageBody, fromAddr ethCommon.Address) {
	var request functions.SecretsSetRequest
	var response functions.SecretsSetResponse
	err := json.Unmarshal(body.Payload, &request)
	if err == nil {
		key := s4.Key{
			Address: fromAddr,
			SlotId:  request.SlotID,
			Version: request.Version,
		}
		record := s4.Record{
			Expiration: request.Expiration,
			Payload:    request.Payload,
		}
		h.lggr.Debugw("handling a secrets_set request", "address", fromAddr, "slotId", request.SlotID, "payloadVersion", request.Version, "expiration", request.Expiration)
		err = h.storage.Put(ctx, &key, &record, request.Signature)
		if err == nil {
			response.Success = true
			promStorageUserUpdatesCount.WithLabelValues().Inc()
		} else {
			response.ErrorMessage = fmt.Sprintf("Failed to set secret: %v", err)
		}
	} else {
		response.ErrorMessage = fmt.Sprintf("Bad request to set secret: %v", err)
	}
	h.sendResponseAndLog(ctx, gatewayId, body, response)
}
```

**File:** core/services/s4/storage.go (L129-152)
```go
func (s *storage) Put(ctx context.Context, key *Key, record *Record, signature []byte) error {
	if key.SlotId >= s.contraints.MaxSlotsPerUser {
		return ErrSlotIdTooBig
	}
	if len(record.Payload) > int(s.contraints.MaxPayloadSizeBytes) {
		return ErrPayloadTooBig
	}
	now := s.clock.Now().UnixMilli()
	if now > record.Expiration {
		return ErrPastExpiration
	}
	expSecs := s.contraints.MaxExpirationLengthSec * 1000
	if expSecs > math.MaxInt64 {
		return fmt.Errorf("expiration seconds overflows int64: %d", expSecs)
	}
	if record.Expiration-now > int64(expSecs) {
		return ErrExpirationTooLong
	}

	envelope := NewEnvelopeFromRecord(key, record)
	signer, err := envelope.GetSignerAddress(signature)
	if err != nil || signer != key.Address {
		return ErrWrongSignature
	}
```

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

**File:** core/scripts/gateway/client/send_request.go (L62-89)
```go
	// build payload (if relevant)
	var payloadJSON []byte
	if *methodName == functions.MethodSecretsSet {
		envelope := s4.Envelope{
			Address:    address.Bytes(),
			SlotID:     *s4SetSlotId,
			Version:    *s4SetVersion,
			Payload:    s4SetPayload,
			Expiration: time.Now().UnixMilli() + *s4SetExpirationPeriod,
		}
		signature, err2 := envelope.Sign(key)
		if err2 != nil {
			fmt.Println("error signing S4 envelope", err2)
			return
		}

		payloadJSON, err2 = json.Marshal(functions.SecretsSetRequest{
			SlotID:     envelope.SlotID,
			Version:    envelope.Version,
			Expiration: envelope.Expiration,
			Payload:    s4SetPayload,
			Signature:  signature,
		})
		if err2 != nil {
			fmt.Println("error marshaling S4 payload", err2)
			return
		}
	}
```

**File:** core/services/gateway/handlers/functions/api.go (L13-19)
```go
type SecretsSetRequest struct {
	SlotID     uint   `json:"slot_id"`
	Version    uint64 `json:"version"`
	Expiration int64  `json:"expiration"`
	Payload    []byte `json:"payload"`
	Signature  []byte `json:"signature"`
}
```
