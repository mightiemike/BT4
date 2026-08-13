This confirms `CreateExternalInitiator` has no fetch/SSRF surface, and existing controls elsewhere (restricted HTTP client, `safeurl` allow/block-list checks) already guard the actual outbound-fetch paths that do exist in the codebase (`BridgeTask.Run`, `HTTPTask.Run`).

Key findings:

- `CreateExternalInitiator` in `core/bridges/orm.go` is a pure SQL INSERT that persists a pre-built `*bridges.ExternalInitiator` (name, url, access_key, salt, hashed_secret, outgoing_secret, outgoing_token) — it performs no outbound HTTP fetch and does not accept a `dotId`/`specId`/cache slot at all. [1](#0-0) 
- Those attacker-cited parameters (`dotId`, `specId`, cached response slots) belong to an entirely different, unrelated ORM method pair, `GetCachedResponse`/`UpsertBridgeResponse`, which only reads/writes a local response cache keyed by `dotId`+`specId` — it does not perform outbound fetches or consume attacker-supplied URLs. [2](#0-1) 
- The `*ExternalInitiator` object is constructed server-side via `bridges.NewExternalInitiator` from a freshly generated `auth.NewToken()`; the `access_key`/`hashed_secret`/`salt` fields are not attacker-controlled secret locations, and the `Create` handler validates the request name via `ValidateExternalInitiator` before persisting. [3](#0-2) 
- The only code path in this codebase that actually performs an outbound "secrets fetch" using a job-owned name is `BridgeTask.Run`/`getBridgeURLFromName`, which resolves the URL via `FindBridge` (a bridge record only creatable/updatable by a privileged node operator through `CreateBridgeType`/`UpdateBridgeType`, not by an unprivileged job/dotID/cache-slot attacker), and that request is issued over the restricted client, which explicitly blocks loopback/private/link-local/multicast destinations. [4](#0-3) [5](#0-4) 
- Generic outbound fetches (e.g. `HTTPTask`) are similarly protected: hardcoded literal URLs use the unrestricted client only when explicitly opted-in via `AllowUnrestrictedNetworkAccess`, while variable-interpolated URLs default to the restricted client, which rejects local/private/internal targets. [6](#0-5) 

There is no code path by which `CreateExternalInitiator` fetches attacker-shaped secret locations, reaches internal resources, or leaks database passwords/blockchain keys — it is a plain database insert with no network I/O, no cache interaction, and no attacker-influenced fetch target. The premise of the question (that job-owned bridge names, dot IDs, and cached response slots feed into `CreateExternalInitiator` to cause an SSRF/secrets-fetch escape) does not match the actual code.

### No Vulnerability found for this question.

### Citations

**File:** core/bridges/orm.go (L16-25)
```go
type ORM interface {
	FindBridge(ctx context.Context, name BridgeName) (bt BridgeType, err error)
	FindBridges(ctx context.Context, name []BridgeName) (bts []BridgeType, err error)
	DeleteBridgeType(ctx context.Context, bt *BridgeType) error
	BridgeTypes(ctx context.Context, offset int, limit int) ([]BridgeType, int, error)
	CreateBridgeType(ctx context.Context, bt *BridgeType) error
	UpdateBridgeType(ctx context.Context, bt *BridgeType, btr *BridgeTypeRequest) error

	GetCachedResponse(ctx context.Context, dotId string, specId int32, maxElapsed time.Duration) ([]byte, error)
	UpsertBridgeResponse(ctx context.Context, dotId string, specId int32, response []byte) error
```

**File:** core/bridges/orm.go (L227-243)
```go
// CreateExternalInitiator inserts a new external initiator
func (o *orm) CreateExternalInitiator(ctx context.Context, externalInitiator *ExternalInitiator) (err error) {
	query := `INSERT INTO external_initiators (name, url, access_key, salt, hashed_secret, outgoing_secret, outgoing_token, created_at, updated_at)
	VALUES (:name, :url, :access_key, :salt, :hashed_secret, :outgoing_secret, :outgoing_token, now(), now())
	RETURNING *
	`
	err = o.transact(ctx, false, func(tx *orm) error {
		var stmt *sqlx.NamedStmt
		stmt, err = tx.ds.PrepareNamedContext(ctx, query)
		if err != nil {
			return pkgerrors.Wrap(err, "failed to prepare named stmt")
		}
		defer stmt.Close()
		return pkgerrors.Wrap(stmt.GetContext(ctx, externalInitiator, externalInitiator), "failed to load external_initiator")
	})
	return pkgerrors.Wrap(err, "CreateExternalInitiator failed")
}
```

**File:** core/web/external_initiators_controller.go (L61-90)
```go
// Create builds and saves a new external initiator
func (eic *ExternalInitiatorsController) Create(c *gin.Context) {
	ctx := c.Request.Context()
	eir := &bridges.ExternalInitiatorRequest{}
	if !eic.App.GetConfig().JobPipeline().ExternalInitiatorsEnabled() {
		err := errors.New("The External Initiator feature is disabled by configuration")
		jsonAPIError(c, http.StatusMethodNotAllowed, err)
		return
	}

	eia := auth.NewToken()
	if err := c.ShouldBindJSON(eir); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	ei, err := bridges.NewExternalInitiator(eia, eir)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	if err := ValidateExternalInitiator(ctx, eir, eic.App.BridgeORM()); err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}
	if err := eic.App.BridgeORM().CreateExternalInitiator(ctx, ei); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
```

**File:** core/services/pipeline/task.bridge.go (L411-417)
```go
func (t *BridgeTask) getBridgeURLFromName(ctx context.Context, name StringParam) (URLParam, error) {
	bt, err := t.orm.FindBridge(ctx, bridges.BridgeName(name))
	if err != nil {
		return URLParam{}, errors.Wrapf(err, "could not find bridge with name '%s'", name)
	}
	return URLParam(bt.URL), nil
}
```

**File:** core/services/pipeline/task.http_test.go (L235-243)
```go
	task.URL = "$(url)"

	vars := pipeline.NewVarsFrom(map[string]any{"url": server.URL})
	result, runInfo = task.Run(t.Context(), logger.TestLogger(t), vars, nil)
	assert.False(t, runInfo.IsPending)
	assert.True(t, runInfo.IsRetryable)
	require.Error(t, result.Error)
	require.Contains(t, result.Error.Error(), "Connections to local/private and multicast networks are disabled")
	require.Nil(t, result.Value)
```

**File:** core/services/pipeline/task.http.go (L60-112)
```go
	var (
		method                         StringParam
		url                            URLParam
		requestData                    MapParam
		allowUnrestrictedNetworkAccess BoolParam
		reqHeaders                     StringSliceParam
	)
	err = stderrors.Join(
		errors.Wrap(ResolveParam(&method, From(NonemptyString(t.Method), "GET")), "method"),
		errors.Wrap(ResolveParam(&url, From(VarExpr(t.URL, vars), NonemptyString(t.URL))), "url"),
		errors.Wrap(ResolveParam(&requestData, From(VarExpr(t.RequestData, vars), JSONWithVarExprs(t.RequestData, vars, false), nil)), "requestData"),
		// Any hardcoded strings used for URL uses the unrestricted HTTP adapter
		// Interpolated variable URLs use restricted HTTP adapter by default
		// You must set allowUnrestrictedNetworkAccess=true on the task to enable variable-interpolated URLs to make restricted network requests
		errors.Wrap(ResolveParam(&allowUnrestrictedNetworkAccess, From(NonemptyString(t.AllowUnrestrictedNetworkAccess), !variableRegexp.MatchString(t.URL))), "allowUnrestrictedNetworkAccess"),
		errors.Wrap(ResolveParam(&reqHeaders, From(NonemptyString(t.Headers), "[]")), "reqHeaders"),
	)
	if err != nil {
		return Result{Error: err}, runInfo
	}

	if len(reqHeaders)%2 != 0 {
		return Result{Error: errors.Errorf("headers must have an even number of elements")}, runInfo
	}

	requestDataJSON, err := json.Marshal(requestData)
	if err != nil {
		return Result{Error: err}, runInfo
	}
	lggr.Debugw("HTTP task: sending request",
		"requestData", string(requestDataJSON),
		"url", url.String(),
		"method", method,
		"reqHeaders", reqHeaders,
		"allowUnrestrictedNetworkAccess", allowUnrestrictedNetworkAccess,
	)

	requestCtx, cancel := httpRequestCtx(ctx, t, t.config)
	defer cancel()

	var client *http.Client
	if allowUnrestrictedNetworkAccess {
		client = t.unrestrictedHTTPClient
	} else {
		client = t.httpClient
	}
	responseBytes, statusCode, respHeaders, start, finish, err := makeHTTPRequest(requestCtx, lggr, method, url, reqHeaders, requestData, client, t.config.DefaultHTTPLimit())
	elapsed := finish.Sub(start).Milliseconds()
	if err != nil {
		if errors.Is(errors.Cause(err), clhttp.ErrDisallowedIP) {
			err = errors.Wrap(err, `connections to local resources are disabled by default, if you are sure this is safe, you can enable on a per-task basis by setting allowUnrestrictedNetworkAccess="true" in the pipeline task spec, e.g. fetch [type="http" method=GET url="$(decode_cbor.url)" allowUnrestrictedNetworkAccess="true"]`)
		}
		return Result{Error: err}, RunInfo{IsRetryable: isRetryableHTTPError(statusCode, err)}
```
