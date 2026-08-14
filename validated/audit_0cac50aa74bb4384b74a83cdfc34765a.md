## Analysis

I found a valid analog. The Gateway's node-facing WebSocket handshake endpoint reproduces the exact bug class from the report: an unauthenticated party can repeatedly trigger non-negligible cryptographic and resource-allocating work (challenge generation via `rand.Read`, ECDSA signature recovery) with no origin/IP-based rate limiting anywhere in the handshake path.

### Title
Gateway WebSocket node handshake endpoint (`StartHandshake`/`FinalizeHandshake`) has no origin-based rate limiting, enabling a DoS vector - (File: `core/services/gateway/network/wsserver.go`)

### Summary
The Gateway's node-connection WebSocket server exposes an HTTP endpoint (`webSocketServer.handleRequest`) that, for every inbound request, decodes an auth header and invokes `ConnectionAcceptor.StartHandshake`, which performs ECDSA signature recovery (`common.ExtractSigner`) and, on success, allocates a random challenge via `rand.Read` and stores handshake state in memory. None of this work is gated by any per-IP/origin or global rate limiter before it executes.

### Finding Description
`webSocketServer.handleRequest` in [1](#0-0)  is the HTTP handler mounted on the Gateway's node server path. It immediately decodes the base64 auth header and calls `s.acceptor.StartHandshake(authBytes)` with no rate limiting applied beforehand.

`StartHandshake`, implemented in `connectionManager.StartHandshake`, unpacks and cryptographically verifies the signer of the auth header (`network.UnpackSignedAuthHeader`, which calls `common.ExtractSigner` — an ECDSA public-key recovery operation), performs map lookups by DON ID and node address, validates the timestamp, and then calls `newAttempt`, which allocates a random challenge with `rand.Read` and stores per-attempt state in `m.connAttempts` under a mutex: [2](#0-1) .

Likewise, on the client side (`gatewayConnector.ChallengeResponse` and `handshake.go`'s `UnpackChallenge`), the same signature-verification cost pattern exists, but the more important issue is the server-side entry point that any external caller can hit repeatedly.

Searching the rest of the Gateway codebase confirms rate limiting exists only *after* a successful handshake, at the message/handler level (e.g., `functionsHandler.HandleLegacyUserMessage`'s `userRateLimiter.Allow`, `gatewayHandler.HandleNodeMessage`'s per-node/global rate limiters) [3](#0-2) [4](#0-3) . There is no equivalent limiter wrapping the handshake/challenge-issuance path itself — the only guard present is a `MaxRequestBytesLimiter`, which is applied *after* the WebSocket upgrade and challenge issuance, for message size only, not request rate [5](#0-4) .

### Impact Explanation
An unauthenticated network peer can send an unlimited number of connection attempts to the Gateway's node WebSocket endpoint. Each attempt forces the server to perform ECDSA signature recovery, mutex-guarded map operations, and secure random byte generation for challenge issuance — all before any authentication succeeds or fails definitively. Because this cost is incurred per-request with no throttle, a malicious actor can drive sustained CPU and memory (attempt-map growth via `m.connAttempts`) load on the Gateway process, degrading or denying availability of the node-connection service for legitimate DON members. This directly maps to the reported bug class: an authentication challenge/response flow whose expensive steps run unthrottled per attempt.

### Likelihood Explanation
The endpoint is reachable by any network client capable of reaching the Gateway's node server port — it is the entry point nodes use to establish DON connectivity, but the HTTP handler itself performs no origin verification prior to invoking the expensive `StartHandshake` logic. No allowlisting or IP-based limiter exists at this layer in the reviewed code, making exploitation straightforward for any party with network access to the port.

### Recommendation
Add an origin/IP-based rate limiter (and/or a global rate limiter) in `webSocketServer.handleRequest` that is checked and incremented *before* calling `s.acceptor.StartHandshake`, so malformed or malicious auth headers are throttled immediately — including on signature-verification failures. Consider adding a failure-scoring mechanism (e.g., temporary IP bans after repeated `ErrChallengeInvalidSignature`/`ErrAuthInvalidNode` failures) analogous to the recommendation in the original report.

### Proof of Concept
A client can repeatedly POST/upgrade to the Gateway's `/node` WebSocket path with syntactically valid (or invalid) base64 auth headers of maximum allowed length (`HandshakeEncodedAuthHeaderMaxLen`). Each request forces the server through `base64` decode, `UnpackSignedAuthHeader` (ECDSA recover), map lookups, and — for auth headers that pass DON/gateway/timestamp checks — a `rand.Read`-based challenge allocation and mutex-protected map insert in `newAttempt`, all prior to any rate-limiting check, as shown in [1](#0-0)  and [2](#0-1) .

### Citations

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

**File:** core/services/gateway/network/wsserver.go (L133-139)
```go
	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		s.lggr.Errorw("failed to get request size limit", "err", err)
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	conn.SetReadLimit(int64(maxRequestBytes))
```

**File:** core/services/gateway/connectionmanager.go (L215-258)
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

**File:** core/services/gateway/handlers/functions/handler.functions.go (L208-219)
```go
func (h *functionsHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	sender := common.HexToAddress(msg.Body.Sender)
	if h.allowlist != nil && !h.allowlist.Allow(sender) {
		h.lggr.Debugw("received a message from a non-allowlisted address", "sender", msg.Body.Sender)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrNotAllowlisted.Error()).Inc()
		return ErrNotAllowlisted
	}
	if h.userRateLimiter != nil && !h.userRateLimiter.Allow(msg.Body.Sender) {
		h.lggr.Debugw("rate-limited", "sender", msg.Body.Sender)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrRateLimited.Error()).Inc()
		return ErrRateLimited
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L230-246)
```go
func (h *gatewayHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	if resp.ID == "" {
		return fmt.Errorf("received response with empty request ID from node %s", nodeAddr)
	}
	h.lggr.Debugw("handling incoming node message", "requestID", resp.ID, "nodeAddr", nodeAddr)
	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementCapabilityNodeThrottled(ctx, nodeAddr, h.lggr)
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementGlobalThrottled(ctx, h.lggr)
		return errors.New("global rate limit exceeded")
	}
```
