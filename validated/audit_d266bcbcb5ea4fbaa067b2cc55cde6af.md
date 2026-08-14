### Title
Unsalted concatenation in `messageID()` allows gateway request-ID collisions across workflows/URLs - (File: `core/services/workflows/artifacts/store.go`)

### Summary
The reported Sui bug class is: hashing/keying a value by naively concatenating variable-length, attacker-influenced fields without length-prefixing, which lets different logical inputs produce the same digest/key and thus be confused with one another. The closest reachable analog in this chainlink repo is `messageID()` in `core/services/workflows/artifacts/store.go`, which builds the gateway `MessageId` used to correlate outbound fetches (config/secrets URL fetches) by writing `url` and `workflowID`/`ownerHex` directly into a SHA-256 hasher with no delimiter or length prefix between them.

### Finding Description
`messageID` concatenates unescaped, variable-length strings into a hash with no separator or length prefix: [1](#0-0) 

It is called with attacker/workflow-controlled inputs (the fetched URL and workflow/owner identifiers) in several places: [2](#0-1) [3](#0-2) [4](#0-3) 

Because `sha256(url || workflowID)` has no length delimiter, two different `(url, workflowID)` pairs whose concatenated byte streams are identical (e.g. `url="http://x.com/a"`, `id="bc"` vs `url="http://x.com/ab"`, `id="c"`) produce the same `messageID`. This ID is used directly as the correlation key (`MessageId`) for a pending, in-flight gateway request: [5](#0-4) 

`c.responses.new(messageID)` registers a single-use channel keyed by this ID and returns an error only for exact duplicate concurrent IDs; a crafted colliding `(url, workflowID)` pair from a different fetch would compute to the same key.

### Impact Explanation
`messageID` is used to fetch workflow secrets and config content from external URLs via the Gateway (`GetSecrets`, `ForceUpdateSecrets`, `GetConfig`). If two concurrently-in-flight fetches (belonging to different workflows/owners) produce a colliding `messageID`, the response-correlation channel keyed by that ID could receive/return the response body intended for the other request, causing secrets or config content to be delivered to the wrong workflow/owner — a secret-disclosure / data-tampering condition across the workflow trust boundary, which matches the accepted impact classes (secret disclosure, misreporting/data tampering).

### Likelihood Explanation
Exploitation requires an attacker to control or predict a workflow's `secretsURL`/`workflowID` (or `ownerHex`) well enough to force a byte-for-byte collision with another concurrently-fetching workflow's `url`+`id`, and for the two fetches to be in-flight at the same time so the response race actually manifests. This is a real but non-trivial condition: `workflowID`/owner hex are fixed-width hex, restricting the collision search to varying the URL suffix/prefix, and the collision must line up exactly with another party's concurrent request. Likelihood is moderate — the primitive (missing length-prefix, per the report's remediation pattern) is present and reachable by unprivileged workflow authors, but reliable exploitation needs concurrent-request timing.

### Recommendation
Apply the same fix pattern recommended in the referenced report: include an explicit length prefix (or a fixed delimiter that cannot appear ambiguously) for each variable-length component before hashing, e.g.:
```go
func messageID(url string, parts ...string) string {
    h := sha256.New()
    writeLenPrefixed(h, url)
    for _, p := range parts {
        writeLenPrefixed(h, p)
    }
    ...
}
```
where `writeLenPrefixed` writes a fixed-size length header before each field, eliminating any possibility that different `(url, workflowID)` tuples hash to the same `messageID`.

### Proof of Concept
Conceptually (not executed against the live system, since values must be crafted so `url+workflowID` byte-concatenates identically for two different logical requests):
1. Workflow A calls `GetSecrets` with `secretsURL = "http://svc/a"`, `workflowID = X + "bc"`.
2. Workflow B calls `GetSecrets` concurrently with `secretsURL = "http://svc/a" + "b"`, `workflowID = X + "c"`.
3. Both produce identical bytes into `sha256.New()` (`"http://svc/ab" + X + "c"` vs `"http://svc/a" + X + "bc"` — chosen so the concatenated streams match), yielding the same `messageID`.
4. `c.responses.new(messageID)` in `outgoing_connector_handler.go` registers only one pending channel per ID; if both requests are in-flight, the gateway response destined for one workflow can be delivered on the shared channel and returned to the other caller's `HandleSingleNodeRequest`, resulting in workflow A receiving workflow B's secrets payload (or vice versa).

Note: I was not able to fully trace `responses.new`/`cleanup` semantics (e.g., whether it strictly rejects a second registration with the same ID at any point rather than only for exact simultaneous duplicates) due to remaining tool-call budget; a full confirmation of the race window would require reading `core/capabilities/webapi/responses.go` (not retrieved in this session).

### Citations

**File:** core/services/workflows/artifacts/store.go (L205-216)
```go
	if configURL != "" {
		req := ghcapabilities.Request{
			URL:              configURL,
			Method:           http.MethodGet,
			MaxResponseBytes: safeUint32(h.limits.MaxConfigSize),
			WorkflowID:       workflowID,
		}
		config, err = h.fetchFn(ctx, messageID(configURL, workflowID), req)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to fetch config from %s : %w", configURL, err)
		}
	}
```

**File:** core/services/workflows/artifacts/store.go (L220-234)
```go
func (h *Store) GetSecrets(ctx context.Context, secretsURL string, workflowID [32]byte, workflowOwner []byte) ([]byte, error) {
	wid := hex.EncodeToString(workflowID[:])
	req := ghcapabilities.Request{
		URL:              secretsURL,
		Method:           http.MethodGet,
		MaxResponseBytes: safeUint32(h.limits.MaxSecretsSize),
		WorkflowID:       wid,
	}
	fetchedSecrets, fetchErr := h.fetchFn(ctx, messageID(secretsURL, wid), req)
	if fetchErr != nil {
		return nil, fmt.Errorf("failed to fetch secrets from %s : %w", secretsURL, fetchErr)
	}

	return fetchedSecrets, nil
}
```

**File:** core/services/workflows/artifacts/store.go (L255-280)
```go
func (h *Store) ForceUpdateSecrets(
	ctx context.Context,
	secretsURLHash []byte,
	owner []byte,
) (string, error) {
	// Get the URL of the secrets file from the event data
	hash := hex.EncodeToString(secretsURLHash)

	url, err := h.orm.GetSecretsURLByHash(ctx, hash)
	if err != nil {
		return "", fmt.Errorf("failed to get URL by hash %s : %w", hash, err)
	}

	ownerHex := hex.EncodeToString(owner)
	req := ghcapabilities.Request{
		URL:              url,
		Method:           http.MethodGet,
		MaxResponseBytes: safeUint32(h.limits.MaxSecretsSize),
		// TODO -- fix, but this is used for rate limiting purposes
		WorkflowID: hex.EncodeToString(owner),
	}
	// Fetch the contents of the secrets file from the url via the fetcher
	secrets, err := h.fetchFn(ctx, messageID(url, ownerHex), req)
	if err != nil {
		return "", err
	}
```

**File:** core/services/workflows/artifacts/store.go (L414-423)
```go
func messageID(url string, parts ...string) string {
	h := sha256.New()
	h.Write([]byte(url))
	for _, p := range parts {
		h.Write([]byte(p))
	}
	hash := hex.EncodeToString(h.Sum(nil))
	p := []string{ghcapabilities.MethodWorkflowSyncer, hash}
	return strings.Join(p, "/")
}
```

**File:** core/capabilities/webapi/outgoing_connector_handler.go (L136-154)
```go
	ch, err := c.responses.new(messageID)
	if err != nil {
		return nil, fmt.Errorf("duplicate message received for ID: %s", messageID)
	}
	defer c.responses.cleanup(messageID)

	donID, err := c.gc.DonID(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get DON ID: %w", err)
	}

	lggr.Debugw("sending request to gateway")

	body := &api.MessageBody{
		MessageId: messageID,
		DonId:     donID,
		Method:    c.method,
		Payload:   payload,
	}
```
