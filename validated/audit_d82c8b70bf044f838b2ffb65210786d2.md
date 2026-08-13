### Title
Auth-header replay causes unbounded pending handshake entries per node address in `connAttempts` map - ([File: core/services/gateway/connectionmanager.go])

### Summary
`connectionManager.StartHandshake` validates a signed auth header's timestamp against `AuthTimestampToleranceSec` but has no nonce/one-time-use tracking, so the exact same signed header bytes can be replayed any number of times while the timestamp remains within tolerance. Each replay creates a brand-new entry in `m.connAttempts` via `newAttempt` with no per-node cap, rate limit, or dedup, allowing unbounded growth of pending-attempt state for a single legitimate node address.

### Finding Description
`StartHandshake` at [1](#0-0)  parses the auth header, resolves `nodeAddress` from the ECDSA signature, checks DON/gateway IDs, and validates only that the header's `Timestamp` is within `AuthTimestampToleranceSec` of the current time — there is no check that this specific signed header (or its timestamp) has not been seen/used before. Since `UnpackSignedAuthHeader` derives `nodeAddress` purely from the signature over the header bytes [2](#0-1) , an attacker without the node's private key cannot forge a *new* header for a different timestamp, but can replay a previously observed, still-valid signed header byte-for-byte as many times as desired within the tolerance window.

Each successful `StartHandshake` call unconditionally calls `newAttempt`, which allocates a new random challenge and inserts a new map entry keyed by a globally incrementing counter (`nodeAddress_<counter>`), with no limit on the number of concurrent entries per `nodeAddress` and no cleanup other than explicit `FinalizeHandshake`/`AbortHandshake` calls [3](#0-2) . The HTTP entrypoint `handleRequest` in `wsserver.go` forwards any base64-decoded header directly to `StartHandshake` with no per-IP or per-node rate limiting before or after this call [4](#0-3) ; the only limiter present (`MaxRequestBytesLimiter`) bounds message size, not handshake attempt frequency.

Because the attacker cannot produce a valid `FinalizeHandshake` response (that requires signing the freshly-generated random `challenge`, which needs the node's private key), they cannot actually take over the node's real connection slot (`nodeState.conn.Reset(conn)` only happens on signature success). However, they can still open many concurrent WebSocket upgrade attempts, each holding a live `connAttempt` entry and (if they never send a follow-up message) potentially a half-open socket, since `conn.ReadMessage()` in `handleRequest` has no explicit deadline configured until after a successful handshake sets `PongTimeoutSec` deadlines.

### Impact Explanation
This enables resource exhaustion against the gateway: unbounded growth of the `connAttempts` map (memory) and, if attackers leave sockets open without responding, accumulation of half-open server-side WebSocket connections, potentially exhausting connection/file-descriptor limits and degrading gateway availability for legitimate DON nodes trying to (re)connect. This matches a denial-of-service / connection-slot exhaustion impact category rather than an authentication bypass, since the attacker cannot complete the challenge-response without the node's private key and thus cannot hijack the node's identity or connection.

### Likelihood Explanation
Feasible for any attacker who has observed one valid signed auth header in transit (e.g., no TLS deployment, or a misconfigured/non-TLS internal network) or via config leakage, as stated in the preconditions. No private key or additional secrets are required — only replay of the exact previously captured bytes within `AuthTimestampToleranceSec` (a configurable window, commonly tens of seconds to minutes). The attack is easily automatable and repeatable within each tolerance window, and the header could be re-used indefinitely in successive windows if re-observed on each legitimate reconnect. It requires network position or leakage to observe a header, so it is not a fully "zero precondition" issue but is exploitable by an unprivileged network observer.

### Recommendation
Add replay protection to the auth-header/timestamp path: track already-used `(nodeAddress, timestamp)` (or a hash of the full header) within the tolerance window and reject duplicates; alternatively use a strictly monotonic timestamp requirement per node (reject timestamps not greater than the last accepted one for that address) plus a cap on the number of concurrent pending `connAttempts` per `nodeAddress` (e.g., replace/reject new attempts when one is already pending for the same address, with a bounded TTL-based eviction), and enforce a read deadline on `conn.ReadMessage()` in `handleRequest` immediately after upgrade so unanswered handshakes are aborted promptly.

### Proof of Concept
Integration test plan:
1. Configure a `connectionManager` with a DON containing a single legitimate node and `AuthTimestampToleranceSec` set to e.g. 30 seconds.
2. Generate one valid signed auth header for the node (`NewAuthHeader` equivalent) and call `StartHandshake(authHeader)` on it N times (e.g., N=1000) within the tolerance window without ever completing `FinalizeHandshake`.
3. Assert that after the N replays, `len(m.connAttempts)` (or an exported metric) grows to N instead of being capped at 1 per `nodeAddress` — i.e., add and assert an invariant such as "at most one pending attempt per `nodeAddress`" is violated.
4. Optionally drive this via `wsserver`'s HTTP endpoint concurrently to also demonstrate accumulation of upgraded-but-unfinalized WebSocket connections, and assert the gateway enforces a per-address rate limit/single-pending-attempt rule (currently absent).

### Citations

**File:** core/services/gateway/connectionmanager.go (L215-243)
```go
func (m *connectionManager) StartHandshake(authHeader []byte) (attemptId string, challenge []byte, err error) {
	m.lggr.Debug("StartHandshake")
	authHeaderElems, signer, err := network.UnpackSignedAuthHeader(authHeader)
	if err != nil {
		return "", nil, errors.Join(network.ErrAuthHeaderParse, err)
	}
	nodeAddress := "0x" + hex.EncodeToString(signer)
	donConnMgr, ok := m.dons[authHeaderElems.DonId]
	if !ok {
		return "", nil, network.ErrAuthInvalidDonId
	}
	nodeState, ok := donConnMgr.nodes[nodeAddress]
	if !ok {
		return "", nil, network.ErrAuthInvalidNode
	}
	if authHeaderElems.GatewayId != m.config.AuthGatewayId {
		return "", nil, network.ErrAuthInvalidGateway
	}
	nowTs := uint32(m.clock.Now().Unix())
	ts := authHeaderElems.Timestamp
	if ts < nowTs-m.config.AuthTimestampToleranceSec || nowTs+m.config.AuthTimestampToleranceSec < ts {
		return "", nil, network.ErrAuthInvalidTimestamp
	}
	attemptId, challenge, err = m.newAttempt(nodeState, nodeAddress, ts)
	if err != nil {
		return "", nil, err
	}
	return attemptId, challenge, nil
}
```

**File:** core/services/gateway/connectionmanager.go (L245-258)
```go
func (m *connectionManager) newAttempt(nodeSt *nodeState, nodeAddress string, timestamp uint32) (string, []byte, error) {
	challengeBytes := make([]byte, m.config.AuthChallengeLen)
	_, err := rand.Read(challengeBytes)
	if err != nil {
		return "", nil, err
	}
	challenge := network.ChallengeElems{Timestamp: timestamp, GatewayId: m.config.AuthGatewayId, ChallengeBytes: challengeBytes}
	m.connAttemptsMu.Lock()
	defer m.connAttemptsMu.Unlock()
	m.connAttemptCounter++
	newId := fmt.Sprintf("%s_%d", nodeAddress, m.connAttemptCounter)
	m.connAttempts[newId] = &connAttempt{nodeState: nodeSt, nodeAddress: nodeAddress, challenge: challenge, timestamp: timestamp}
	return newId, network.PackChallenge(&challenge), nil
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

**File:** core/services/gateway/network/wsserver.go (L100-118)
```go
func (s *webSocketServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	authHeader := r.Header.Get(WsServerHandshakeAuthHeaderName)
	if len(authHeader) > HandshakeEncodedAuthHeaderMaxLen {
		s.lggr.Debugw("received auth header is too large", "len", len(authHeader))
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	authBytes, err := base64.StdEncoding.DecodeString(authHeader)
	if err != nil {
		s.lggr.Debugw("received auth header can't be base64-decoded", "err", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	attemptId, challenge, err := s.acceptor.StartHandshake(authBytes)
	if err != nil {
		s.lggr.Debugw("received invalid auth header", "err", err)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
```
