### Title
Out-of-range `Flags` index in Functions requests bypasses configured `MaxRequestSizesList`/`MaxSecretsSizesList` size ceilings - ([File: core/services/functions/listener.go])

### Summary
`functionsListener.getMaxCBORsize` and `getMaxSecretsSize` index into node-operator-configured size-limit arrays using a byte taken directly from the attacker-supplied on-chain `Flags` field. When the attacker supplies an index outside the configured list length, the functions silently fall back to a deprecated, commonly-unset field (`MaxRequestSizeBytes`, default `0`) or to `math.MaxUint32`, both of which effectively disable size enforcement instead of applying any configured ceiling.

### Finding Description
`request.Flags` is a raw `[32]byte` array populated from the on-chain `OracleRequest` log emitted by the requester's own transaction, and is passed unmodified into `handleOracleRequestV1` [1](#0-0) .

The size-limit lookups index this array with attacker-controlled bytes at fixed positions (`FlagCBORMaxSize`, `FlagSecretsMaxSize`): [2](#0-1) 

- `getMaxCBORsize`: if `flags[FlagCBORMaxSize] >= len(MaxRequestSizesList)`, it returns `l.pluginConfig.MaxRequestSizeBytes`, marked `// deprecated`. This field defaults to `0` when node operators have migrated to `MaxRequestSizesList` and never set the legacy field.
- `getMaxSecretsSize`: if `flags[FlagSecretsMaxSize] >= len(MaxSecretsSizesList)`, it returns `math.MaxUint32` with the comment `// not enforced if not configured`.

The returned value is used in `parseCBOR`: [3](#0-2) 
Critically, `if maxSizeBytes > 0 && uint32(len(cborData)) > maxSizeBytes` means a returned `0` **disables the size check entirely**, not just sets an unusually large limit. Since `flags[FlagCBORMaxSize]` is a single byte fully controlled by the requester, and `MaxRequestSizesList` is typically a short list (a handful of tiers), the attacker can trivially pick any index ≥ `len(MaxRequestSizesList)` (e.g., 255) to force the `0`/unbounded fallback, even though the operator explicitly configured tiered size limits via `MaxRequestSizesList`.

The oversized `requestData` (and, if `LocationRemote`/`LocationDONHosted` secrets flow is used, `nodeProvidedSecrets`) is then forwarded into `handleRequest` and ultimately into `eaClient.RunComputation`, which JSON-marshals the full payload and POSTs it to the bridge external adapter [4](#0-3) [5](#0-4) .

No other check intercepts this: `parseCBOR`'s own guard is the only requestData size gate before the CBOR-decoded struct is used, and there is no independent re-validation of `requestData` size before it's serialized into the EA payload.

### Impact Explanation
This allows a requester to bypass the node operator's intended `MaxRequestSizesList` (and, in the secrets case, `MaxSecretsSizesList`) ceilings simply by setting an out-of-range flag byte, forcing the fallback to an unbounded/deprecated value. For CBOR request data this is a genuine amplification path since `request.Data` originates from calldata in the requester's own transaction and is not otherwise size-checked before being forwarded to the bridge/external adapter, enabling oversized-payload DoS against the external adapter and rate-limit/config bypass. For secrets, the practical impact is more limited because `nodeProvidedSecrets` size is also indirectly bounded elsewhere (e.g., `externalAdapterClient.maxResponseBytes` via `http.MaxBytesReader` on `FetchEncryptedSecrets`, and `DecryptionQueueConfig.MaxCiphertextBytes` for threshold decryption), but the intended tiered ceiling is still bypassed as a defense-in-depth control.

### Likelihood Explanation
Fully attacker-controlled and requires no privilege: the attacker only needs to submit a Functions request transaction with an arbitrary `Flags` value (a single out-of-range byte), which is a normal, unprivileged on-chain interaction. It is deterministic and repeatable on every request.

### Recommendation
Do not fall back to an unbounded/deprecated value on out-of-range indices. When `flags[FlagCBORMaxSize]`/`flags[FlagSecretsMaxSize]` is out of range of the configured list, either (a) reject the request as invalid/user error, or (b) clamp to the smallest/most restrictive configured size in the list, rather than defaulting to `pluginConfig.MaxRequestSizeBytes` (which may be `0`) or `math.MaxUint32`.

### Proof of Concept
Add a unit test in `core/services/functions/listener_test.go`:
1. Configure `pluginConfig.MaxRequestSizesList = []uint32{1024, 2048}` and leave `MaxRequestSizeBytes` at its zero value (simulating an operator who only adopted the new list-based config).
2. Construct `flags := RequestFlags{}`; set `flags[FlagCBORMaxSize] = 255` (out of range).
3. Call `l.getMaxCBORsize(flags)` and assert it returns `0` (i.e., unbounded/no enforcement) instead of a finite bound derived from the configured list.
4. Feed a CBOR payload larger than 2048 bytes into `l.parseCBOR(requestID, largeCBORData, l.getMaxCBORsize(flags))` and assert it succeeds (no "request too big" error), demonstrating the configured ceiling was bypassed.
5. Repeat analogously for `MaxSecretsSizesList`/`getMaxSecretsSize`, asserting it returns `math.MaxUint32` for an out-of-range `flags[FlagSecretsMaxSize]`.

### Citations

**File:** core/services/functions/listener.go (L276-290)
```go
func (l *functionsListener) getMaxCBORsize(flags RequestFlags) uint32 {
	idx := flags[FlagCBORMaxSize]
	if int(idx) >= len(l.pluginConfig.MaxRequestSizesList) {
		return l.pluginConfig.MaxRequestSizeBytes // deprecated
	}
	return l.pluginConfig.MaxRequestSizesList[idx]
}

func (l *functionsListener) getMaxSecretsSize(flags RequestFlags) uint32 {
	idx := flags[FlagSecretsMaxSize]
	if int(idx) >= len(l.pluginConfig.MaxSecretsSizesList) {
		return math.MaxUint32 // not enforced if not configured
	}
	return l.pluginConfig.MaxSecretsSizesList[idx]
}
```

**File:** core/services/functions/listener.go (L333-365)
```go
func (l *functionsListener) handleOracleRequestV1(request *evmconfig.OracleRequest) {
	defer l.shutdownWaitGroup.Done()
	l.logger.Infow("handleOracleRequestV1: oracle request v1 received", "requestID", formatRequestId(request.RequestId))
	ctx, cancel := l.getNewHandlerContext()
	defer cancel()

	callbackGasLimit := uint32(request.CallbackGasLimit)
	newReq := &Request{
		RequestID:                  request.RequestId,
		RequestTxHash:              &request.TxHash,
		ReceivedAt:                 time.Now(),
		Flags:                      request.Flags[:],
		CallbackGasLimit:           &callbackGasLimit,
		CoordinatorContractAddress: &request.CoordinatorContract,
		OnchainMetadata:            request.OnchainMetadata,
	}
	if err := l.pluginORM.CreateRequest(ctx, newReq); err != nil {
		if errors.Is(err, ErrDuplicateRequestID) {
			l.logger.Warnw("handleOracleRequestV1: received a log with duplicate request ID", "requestID", formatRequestId(request.RequestId), "err", err)
		} else {
			l.logger.Errorw("handleOracleRequestV1: failed to create a DB entry for new request", "requestID", formatRequestId(request.RequestId), "err", err)
		}
		return
	}

	promRequestReceived.WithLabelValues(l.contractAddressHex).Inc()
	promRequestDataSize.WithLabelValues(l.contractAddressHex).Observe(float64(len(request.Data)))
	requestData, err := l.parseCBOR(request.RequestId, request.Data, l.getMaxCBORsize(request.Flags))
	if err != nil {
		l.setError(ctx, request.RequestId, USER_ERROR, []byte(err.Error()))
		return
	}
	err = l.handleRequest(ctx, request.RequestId, request.SubscriptionId, request.SubscriptionOwner, request.Flags, requestData)
```

**File:** core/services/functions/listener.go (L371-384)
```go
func (l *functionsListener) parseCBOR(requestId RequestID, cborData []byte, maxSizeBytes uint32) (*RequestData, error) {
	if maxSizeBytes > 0 && uint32(len(cborData)) > maxSizeBytes {
		l.logger.Errorw("request too big", "requestID", formatRequestId(requestId), "requestSize", len(cborData), "maxRequestSize", maxSizeBytes)
		return nil, fmt.Errorf("request too big (max %d bytes)", maxSizeBytes)
	}

	var requestData RequestData
	if err := cbor.ParseDietCBORToStruct(cborData, &requestData); err != nil {
		l.logger.Errorw("failed to parse CBOR", "requestID", formatRequestId(requestId), "err", err)
		return nil, errors.New("CBOR parsing error")
	}

	return &requestData, nil
}
```

**File:** core/services/functions/listener.go (L415-422)
```go
	maxSecretsSize := l.getMaxSecretsSize(flags)
	if uint32(len(nodeProvidedSecrets)) > maxSecretsSize {
		l.logger.Errorw("secrets size too big", "requestID", requestIDStr, "secretsSize", len(nodeProvidedSecrets), "maxSecretsSize", maxSecretsSize)
		l.setError(ctx, requestID, USER_ERROR, []byte("secrets size too big"))
		return nil // user error
	}

	computationResult, computationError, domains, err := eaClient.RunComputation(ctx, requestIDStr, l.job.Name.ValueOrZero(), subscriptionOwner.Hex(), subscriptionId, flags, nodeProvidedSecrets, requestData)
```

**File:** core/services/functions/external_adapter_client.go (L126-154)
```go
func (ea *externalAdapterClient) RunComputation(
	ctx context.Context,
	requestId string,
	jobName string,
	subscriptionOwner string,
	subscriptionId uint64,
	flags RequestFlags,
	nodeProvidedSecrets string,
	requestData *RequestData,
) (userResult, userError []byte, domains []string, err error) {
	requestData.Secrets = nil // secrets are passed in nodeProvidedSecrets

	payload := requestPayload{
		Endpoint:            "lambda",
		RequestId:           requestId,
		JobName:             jobName,
		SubscriptionOwner:   subscriptionOwner,
		SubscriptionId:      subscriptionId,
		Flags:               flags,
		NodeProvidedSecrets: nodeProvidedSecrets,
		Data:                requestData,
	}

	userResult, userError, domains, err = ea.request(ctx, payload, requestId, jobName, "run_computation")
	if err != nil {
		return nil, nil, nil, errors.Wrap(err, "error running computation")
	}

	return userResult, userError, domains, nil
```
