No vulnerability found for this question.

`MarshalBridgeMetaData` is a pure JSON marshal/unmarshal helper that converts a `BridgeMetaData{LatestAnswer, UpdatedAt}` struct into a `map[string]any` for injection into pipeline `jobRun.meta`. It takes no attacker-controlled bridge names, dot IDs, or auth tokens as input — its only inputs are `*big.Int` values sourced from the OCR data source's internal in-memory state (`ds.current`, updated only from the node's own pipeline run results). [1](#0-0) [2](#0-1) 

The premise linking this function to external-initiator authentication, bridge name identity binding, or cache-key collisions does not hold: authentication for external initiators is handled entirely separately via `AuthenticateExternalInitiator`/`bridges.AuthenticateExternalInitiator` (constant-time hash comparison keyed by `AccessKey`/`Secret`), and bridge task caching uses `dotId`+`specId` scoped to the owning job's own pipeline spec via `responseKey` in `core/bridges/cache.go`, never touching `MarshalBridgeMetaData`. [3](#0-2) [4](#0-3) 

There is no code path by which unprivileged attacker-controlled bridge names, dot IDs, or cache slots flow into `MarshalBridgeMetaData`, and no auth/identity-binding invariant exists inside or adjacent to that function to break.

### Citations

**File:** core/bridges/bridge_type.go (L130-141)
```go
func MarshalBridgeMetaData(latestAnswer *big.Int, updatedAt *big.Int) (map[string]any, error) {
	b, err := json.Marshal(&BridgeMetaData{LatestAnswer: latestAnswer, UpdatedAt: updatedAt})
	if err != nil {
		return nil, err
	}
	var mp map[string]any
	err = json.Unmarshal(b, &mp)
	if err != nil {
		return nil, err
	}
	return mp, nil
}
```

**File:** core/services/ocrcommon/data_source.go (L162-183)
```go
func (ds *inMemoryDataSource) updateAnswer(a *big.Int) {
	ds.mu.Lock()
	defer ds.mu.Unlock()
	ds.current = bridges.BridgeMetaData{
		LatestAnswer: a,
		UpdatedAt:    big.NewInt(time.Now().Unix()),
	}
}

func (ds *inMemoryDataSource) currentAnswer() (*big.Int, *big.Int) {
	ds.mu.RLock()
	defer ds.mu.RUnlock()
	return ds.current.LatestAnswer, ds.current.UpdatedAt
}

// The context passed in here has a timeout of (ObservationTimeout + ObservationGracePeriod).
// Upon context cancellation, its expected that we return any usable values within ObservationGracePeriod.
func (ds *inMemoryDataSource) executeRun(ctx context.Context) (*pipeline.Run, pipeline.TaskRunResults, error) {
	md, err := bridges.MarshalBridgeMetaData(ds.currentAnswer())
	if err != nil {
		ds.lggr.Warnf("unable to attach metadata for run, err: %v", err)
	}
```

**File:** core/web/auth/auth.go (L119-141)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}
```

**File:** core/bridges/cache.go (L216-234)
```go
func (c *Cache) latestValue(dotId string, specId int32) (BridgeResponse, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()

	cached, inCache := c.bridgeLastValueCache[responseKey(dotId, specId)]

	return cached, inCache
}

func (c *Cache) setValue(dotId string, specId int32, resp BridgeResponse) {
	c.mu.Lock()
	defer c.mu.Unlock()

	c.bridgeLastValueCache[responseKey(dotId, specId)] = resp
}

func responseKey(dotId string, specId int32) string {
	return fmt.Sprintf("%s||%d", dotId, specId)
}
```
