### Title
Off-chain Functions heartbeat path executes billed DON requests without verifying `SubscriptionId`/`SubscriptionOwner` against on-chain state - ([File: core/services/functions/connector_handler.go])

### Summary
The Chainlink Functions gateway connector accepts an off-chain "heartbeat" request that self-declares `SubscriptionId`, `SubscriptionOwner`, and `RequestInitiator`. The handler only checks internal self-consistency of these fields (that they match the message sender) and never verifies them against the on-chain Functions subscription registry, unlike the normal on-chain request path where the `FunctionsRouter` contract enforces subscription ownership and balance before emitting a request. This mirrors the LiFi report's root cause: trusting off-chain-supplied values (ids, owners, limits) instead of verifying them against the authoritative on-chain source before performing privileged/billed actions.

### Finding Description
`functionsConnectorHandler.HandleGatewayMessage` routes `functions.MethodHeartbeat` straight to `handleHeartbeat` with **no** balance/ownership check at all (the `GetMaxUserBalance` check only guards `MethodSecretsSet`): [1](#0-0) 

`handleHeartbeat` unmarshals a client-controlled `OffchainRequest` JSON payload and only validates that the self-declared `RequestInitiator` and `SubscriptionOwner` fields equal the authenticated sender address, plus a timestamp freshness check. The `SubscriptionId` field is taken as-is with no on-chain lookup verifying that this ID exists, is owned by `SubscriptionOwner`, or has sufficient funds: [2](#0-1) 

The `OffchainRequest` struct shows `SubscriptionId`/`SubscriptionOwner` are plain fields fully controlled by the payload: [3](#0-2) 

Once accepted, the request is dispatched asynchronously to `listener.HandleOffchainRequest`, which creates a DB entry and immediately proceeds to `handleRequest` (compute execution / DON pipeline) using the attacker-supplied `SubscriptionId`: [4](#0-3) 

In the on-chain flow, the `FunctionsRouter` contract validates subscription ownership and balance atomically before the request event is emitted, which the listener then consumes. The off-chain heartbeat path bypasses this trust boundary entirely — it is a parallel ingestion route that lets an "allowed heartbeat initiator" trigger full DON computation (source-code execution, threshold secret decryption, OCR reporting) for an arbitrary, unverified `SubscriptionId` with no corresponding on-chain funding guarantee.

### Impact Explanation
Any address permitted in `allowedHeartbeatInitiators` (config-driven, not a privileged on-chain role) can cause DON nodes to execute arbitrary user JavaScript, invoke threshold secret decryption, and run the full request pipeline while claiming an arbitrary `SubscriptionId` that is never validated against the real on-chain subscription/balance state at this layer. This enables free consumption of DON compute resources and generation of reports that reference subscriptions the caller may not legitimately be entitled to bill, undermining the billing/authorization model that the on-chain `FunctionsRouter` is supposed to enforce. This falls under "unsafe transaction/workflow execution" and "unauthorized privileged node action" categories.

### Likelihood Explanation
Likelihood is moderate: it requires the caller to already be present in the `allowedHeartbeatInitiators` allowlist, which somewhat narrows exposure, but nothing else prevents a listed initiator (an off-chain/API-driven identity, analogous to the LiFi API caller) from supplying a fabricated `SubscriptionId`/mismatched subscription state, since the handler performs no on-chain cross-check before triggering execution.

### Recommendation
Before dispatching `handleOffchainRequest`, verify `SubscriptionId` and `SubscriptionOwner` against the on-chain `OnchainSubscriptions` accessor (already used elsewhere, e.g. `GetMaxUserBalance`) to confirm the subscription exists, is owned by the claimed owner, and has sufficient balance — mirroring the checks performed for `MethodSecretsSet`. Do not rely solely on payload self-consistency (`RequestInitiator == fromAddr`, `SubscriptionOwner == fromAddr`) as a substitute for on-chain verification.

### Proof of Concept
1. Obtain an address included in the node's `AllowedHeartbeatInitiators` config.
2. Send a `MethodHeartbeat` gateway message with a valid signature, setting `RequestInitiator` and `SubscriptionOwner` to your own address (satisfies the self-consistency checks) but `SubscriptionId` to an arbitrary/nonexistent value.
3. Observe `handleHeartbeat` accepts the request without any call into `OnchainSubscriptions` for that specific ID, and dispatches `handleOffchainRequest` → `listener.HandleOffchainRequest`, causing the DON to execute the supplied `RequestData.Source` and generate an OCR report keyed to the fabricated subscription.

### Citations

**File:** core/services/functions/connector_handler.go (L143-163)
```go
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
}
```

**File:** core/services/functions/connector_handler.go (L240-282)
```go
func (h *functionsConnectorHandler) handleHeartbeat(ctx context.Context, gatewayId string, requestBody *api.MessageBody, fromAddr ethCommon.Address) {
	var request *OffchainRequest
	err := json.Unmarshal(requestBody.Payload, &request)
	if err != nil {
		h.sendResponseAndLog(ctx, gatewayId, requestBody, internalErrorResponse(fmt.Sprintf("failed to unmarshal request: %v", err)))
		return
	}
	if _, ok := h.allowedHeartbeatInitiators[requestBody.Sender]; !ok {
		h.sendResponseAndLog(ctx, gatewayId, requestBody, internalErrorResponse("sender not allowed to send heartbeat requests"))
		return
	}
	if !bytes.Equal(request.RequestInitiator, fromAddr.Bytes()) {
		h.sendResponseAndLog(ctx, gatewayId, requestBody, internalErrorResponse("RequestInitiator doesn't match sender"))
		return
	}
	if !bytes.Equal(request.SubscriptionOwner, fromAddr.Bytes()) {
		h.sendResponseAndLog(ctx, gatewayId, requestBody, internalErrorResponse("SubscriptionOwner doesn't match sender"))
		return
	}
	if request.Timestamp < uint64(time.Now().Unix())-uint64(h.requestTimeoutSec) {
		h.sendResponseAndLog(ctx, gatewayId, requestBody, internalErrorResponse("Request is too old"))
		return
	}

	internalId := InternalId(fromAddr.Bytes(), request.RequestId)
	request.RequestId = internalId[:]
	h.lggr.Infow("handling offchain heartbeat", "messageId", requestBody.MessageId, "internalId", internalId, "sender", requestBody.Sender)
	h.mu.Lock()
	response, ok := h.heartbeatRequests[internalId]
	if !ok { // new request
		response = &HeartbeatResponse{
			Status:     RequestStatePending,
			ReceivedTs: uint64(time.Now().Unix()),
		}
		h.cacheNewRequestLocked(internalId, response)
		h.shutdownWaitGroup.Add(1)
		go h.handleOffchainRequest(request)
	}
	responseToSend := *response
	h.mu.Unlock()
	requestBody.Receiver = requestBody.Sender
	h.sendResponseAndLog(ctx, gatewayId, requestBody, responseToSend)
}
```

**File:** core/services/functions/request.go (L16-23)
```go
type OffchainRequest struct {
	RequestId         []byte      `json:"requestId"`
	RequestInitiator  []byte      `json:"requestInitiator"`
	SubscriptionId    uint64      `json:"subscriptionId"`
	SubscriptionOwner []byte      `json:"subscriptionOwner"`
	Timestamp         uint64      `json:"timestamp"`
	Data              RequestData `json:"data"`
}
```

**File:** core/services/functions/listener.go (L292-330)
```go
func (l *functionsListener) HandleOffchainRequest(ctx context.Context, request *OffchainRequest) error {
	if request == nil {
		return errors.New("HandleOffchainRequest: received nil request")
	}
	if len(request.RequestId) != RequestIDLength {
		return fmt.Errorf("HandleOffchainRequest: invalid request ID length %d", len(request.RequestId))
	}
	if len(request.SubscriptionOwner) != common.AddressLength || len(request.RequestInitiator) != common.AddressLength {
		return errors.New("HandleOffchainRequest: SubscriptionOwner and RequestInitiator must be set to valid addresses")
	}
	if request.Timestamp < uint64(time.Now().Unix()-int64(l.pluginConfig.RequestTimeoutSec)) {
		return errors.New("HandleOffchainRequest: request timestamp is too old")
	}

	var requestId RequestID
	copy(requestId[:], request.RequestId[:32])
	subscriptionOwner := common.BytesToAddress(request.SubscriptionOwner)
	senderAddr := common.BytesToAddress(request.RequestInitiator)
	emptyTxHash := common.Hash{}
	zeroCallbackGasLimit := uint32(0)
	newReq := &Request{
		RequestID:        requestId,
		RequestTxHash:    &emptyTxHash,
		ReceivedAt:       time.Now(),
		Flags:            []byte{},
		CallbackGasLimit: &zeroCallbackGasLimit,
		// use sender address in place of coordinator contract to keep batches uniform
		CoordinatorContractAddress: &senderAddr,
		OnchainMetadata:            []byte(OffchainRequestMarker),
	}
	if err := l.pluginORM.CreateRequest(ctx, newReq); err != nil {
		if errors.Is(err, ErrDuplicateRequestID) {
			l.logger.Warnw("HandleOffchainRequest: received duplicate request ID", "requestID", formatRequestId(requestId), "err", err)
		} else {
			l.logger.Errorw("HandleOffchainRequest: failed to create a DB entry for new request", "requestID", formatRequestId(requestId), "err", err)
		}
		return err
	}
	return l.handleRequest(ctx, requestId, request.SubscriptionId, subscriptionOwner, RequestFlags{}, &request.Data)
```
