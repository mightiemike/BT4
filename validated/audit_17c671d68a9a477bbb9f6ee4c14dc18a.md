Confirmed: `JobsController.Update` in `core/web/jobs_controller.go` deletes the existing job before the new job spec has been fully validated end-to-end by `AddJobV2`/`CreateJob`, since keystore/DB-level checks (e.g. `ErrNoSuchKeyBundle`, `ErrNoSuchTransmitterKey`, `ErrNoSuchSendingKey`, duplicate contract address) only run inside `orm.CreateJob` — after the old job is already gone. This mirrors the reported bug class: an "update" operation that mutates/removes the prior working state before the replacement is guaranteed to succeed, leaving the system bricked (job removed, nothing running) if the second step fails.

### Title
Job Update Deletes Existing Job Before New Job Spec Passes Full Validation, Bricking the Job on Failure - (File: core/web/jobs_controller.go)

### Summary
`JobsController.Update` (`core/web/jobs_controller.go:170-215`) performs a delete-then-create sequence to "update" a job: it deletes the existing job via `jc.App.DeleteJob` and only then attempts to create the replacement via `jc.App.AddJobV2`. TOML-level validation (`validateJobSpec`) happens before the delete, but deeper validation — keystore key matching, transmitter/sending-key existence, duplicate contract address checks, etc. — happens inside `job.ORM.CreateJob` [1](#0-0)  and `job.ValidateKeyStoreMatch` [2](#0-1) , which only runs after the old job has already been removed.

### Finding Description
The `Update` handler is: [3](#0-2) 

The sequence is:
1. `validateJobSpec` parses/validates the TOML into a `job.Job` struct (type-level validation only).
2. `jc.App.DeleteJob(ctx, jb.ID)` removes the currently running job.
3. `jc.App.AddJobV2(ctx, &jb)` attempts to persist and start the new job, which internally calls `jobSpawner.CreateJob` → `orm.CreateJob` [4](#0-3) .

`orm.CreateJob` performs validations that cannot be checked from TOML alone, e.g. verifying an OCR/OCR2 key bundle exists in the keystore, that a transmitter/sending key exists, or that no duplicate `contract_address` already exists for the chain [1](#0-0) . If any of these fail, `AddJobV2` returns an error, but the original job was already deleted in step 2 — there is no rollback. The node operator is left with neither the old job nor a working new one, exactly analogous to the `UpdateManager.update` bug where the new verifier is activated before its validators are set, bricking the update path.

### Impact Explanation
A node operator performing what should be a safe "update" of an existing job (e.g. changing a bridge name, contract address, or transmitter key with a typo) can silently lose the previously working job if the new spec fails a keystore/ORM-level check. This is a legitimate node-management action gone wrong due to the incorrect ordering of destructive vs. validating operations, and it can disrupt oracle/automation/CCIP job execution on the node until manually recreated.

### Likelihood Explanation
This triggers whenever an update request passes TOML parsing but fails one of the deeper checks in `orm.CreateJob` (wrong/missing key bundle ID, wrong transmitter address, sending-key typos, duplicate contract address) — plausible operator error scenarios, not requiring any malicious input. The `Update` endpoint is reachable via the standard job-management API/UI used for routine job maintenance.

### Recommendation
Reorder the operation so the new job spec is fully validated/creatable before the old job is deleted — e.g., attempt `AddJobV2` first (or run the equivalent create/validate path without persisting) and only delete the old job once the new one is confirmed to be created successfully; alternatively wrap delete+create in a single transaction so failures roll back automatically.

### Proof of Concept
1. Create a working OCR2 job referencing an existing `OCRKeyBundleID`.
2. Call `PUT /v2/jobs/:ID` with `web.UpdateJobRequest{TOML: updatedSpec}` where `updatedSpec` references a non-existent `keyBundleID` (valid TOML, passes `validateJobSpec`, but fails `tx.keyStore.OCR2().Get(...)` in `orm.CreateJob`) [5](#0-4) .
3. Observe: `jc.App.DeleteJob` already removed the original job (`core/web/jobs_controller.go:193`), then `AddJobV2` fails with `ErrNoSuchKeyBundle`, returned as `500`/`400` — the job is gone and not replaced, matching the request-flow seen in `TestJobsController_Update_HappyPath` [6](#0-5)  but with a failing second step instead of a succeeding one.

### Citations

**File:** core/services/job/orm.go (L217-248)
```go
			if jb.OCROracleSpec.EncryptedOCRKeyBundleID != nil {
				_, err := tx.keyStore.OCR().Get(jb.OCROracleSpec.EncryptedOCRKeyBundleID.String())
				if err != nil {
					return errors.Wrapf(ErrNoSuchKeyBundle, "no key bundle with id: %x", jb.OCROracleSpec.EncryptedOCRKeyBundleID)
				}
			}
			if jb.OCROracleSpec.TransmitterAddress != nil {
				_, err := tx.keyStore.Eth().Get(ctx, jb.OCROracleSpec.TransmitterAddress.Hex())
				if err != nil {
					return errors.Wrapf(ErrNoSuchTransmitterKey, "no key matching transmitter address: %s", jb.OCROracleSpec.TransmitterAddress.Hex())
				}
			}

			newChainID := jb.OCROracleSpec.EVMChainID
			existingSpec := new(OCROracleSpec)
			err := tx.ds.GetContext(ctx, existingSpec, `SELECT * FROM ocr_oracle_specs WHERE contract_address = $1 and (evm_chain_id = $2 or evm_chain_id IS NULL) LIMIT 1;`,
				jb.OCROracleSpec.ContractAddress, newChainID,
			)

			if !errors.Is(err, sql.ErrNoRows) {
				if err != nil {
					return errors.Wrap(err, "failed to validate OffchainreportingOracleSpec on creation")
				}

				return errors.Errorf("a job with contract address %s already exists for chain ID %s", jb.OCROracleSpec.ContractAddress, newChainID)
			}

			specID, err := tx.insertOCROracleSpec(ctx, jb.OCROracleSpec)
			if err != nil {
				return fmt.Errorf("failed to create OCROracleSpec for jobSpec: %w", err)
			}
			jb.OCROracleSpecID = &specID
```

**File:** core/services/job/orm.go (L250-255)
```go
			if jb.OCR2OracleSpec.OCRKeyBundleID.Valid {
				_, err := tx.keyStore.OCR2().Get(jb.OCR2OracleSpec.OCRKeyBundleID.String)
				if err != nil {
					return errors.Wrapf(ErrNoSuchKeyBundle, "no key bundle with id: %q", jb.OCR2OracleSpec.OCRKeyBundleID.ValueOrZero())
				}
			}
```

**File:** core/services/job/orm.go (L608-620)
```go
// ValidateKeyStoreMatch confirms that the key has a valid match in the keystore
func ValidateKeyStoreMatch(ctx context.Context, spec *OCR2OracleSpec, keyStore keystore.Master, key string) (err error) {
	switch spec.PluginType {
	case types.LLO:
		_, err = keyStore.CSA().Get(key)
		if err != nil {
			err = errors.Errorf("no CSA key matching: %q", key)
		}
	default:
		err = validateKeyStoreMatchForRelay(ctx, spec.Relay, keyStore, key)
	}
	return
}
```

**File:** core/web/jobs_controller.go (L167-215)
```go
// Update validates a new TOML for an existing job, stops and deletes existing job, saves and starts a new job.
// Example:
// "PUT <application>/jobs/:ID"
func (jc *JobsController) Update(c *gin.Context) {
	request := UpdateJobRequest{}
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	jb, status, err := jc.validateJobSpec(c.Request.Context(), request.TOML)
	if err != nil {
		jsonAPIError(c, status, err)
		return
	}

	err = jb.SetID(c.Param("ID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), 5*time.Second)
	defer cancel()

	// If the provided job id is not matching any job, delete will fail with 404 leaving state unchanged.
	err = jc.App.DeleteJob(ctx, jb.ID)
	// Error can be either come from ORM or from the activeJobs map.
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || strings.Contains(err.Error(), "job not found") {
			jsonAPIError(c, http.StatusNotFound, errors.Wrap(err, "failed to update job"))
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	err = jc.App.AddJobV2(ctx, &jb)
	if err != nil {
		if errors.Is(errors.Cause(err), job.ErrNoSuchKeyBundle) || errors.As(err, &keystore.KeyNotFoundError{}) || errors.Is(errors.Cause(err), job.ErrNoSuchTransmitterKey) || errors.Is(errors.Cause(err), job.ErrNoSuchSendingKey) {
			jsonAPIError(c, http.StatusBadRequest, err)
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	jsonAPIResponse(c, presenters.NewJobResource(jb), jb.Type.String())
}
```

**File:** core/services/chainlink/application.go (L1099-1101)
```go
func (app *ChainlinkApplication) AddJobV2(ctx context.Context, j *job.Job) error {
	return app.jobSpawner.CreateJob(ctx, nil, j)
}
```

**File:** core/web/jobs_controller_test.go (L428-493)
```go
func TestJobsController_Update_HappyPath(t *testing.T) {
	t.Parallel()
	ctx := t.Context()
	cfg := configtest.NewGeneralConfig(t, func(c *chainlink.Config, s *chainlink.Secrets) {
		c.OCR.Enabled = new(true)
		c.P2P.V2.Enabled = new(true)
		c.P2P.V2.ListenAddresses = &[]string{fmt.Sprintf("127.0.0.1:%d", freeport.GetOne(t))}
		c.P2P.PeerID = &cltest.DefaultP2PPeerID
	})
	app := cltest.NewApplicationWithConfigAndKey(t, cfg, cltest.DefaultP2PKey)

	require.NoError(t, app.KeyStore.OCR().Add(ctx, cltest.DefaultOCRKey))
	require.NoError(t, app.Start(ctx))

	_, bridge := cltest.MustCreateBridge(t, app.GetDB(), cltest.BridgeOpts{})
	_, bridge2 := cltest.MustCreateBridge(t, app.GetDB(), cltest.BridgeOpts{})

	client := app.NewHTTPClient(nil)

	var jb job.Job
	ocrspec := testspecs.GenerateOCRSpec(testspecs.OCRSpecParams{
		DS1BridgeName: bridge.Name.String(),
		DS2BridgeName: bridge2.Name.String(),
		Name:          "old OCR job",
		EVMChainID:    cltest.FixtureChainID.String(),
	})
	err := toml.Unmarshal([]byte(ocrspec.Toml()), &jb)
	require.NoError(t, err)

	// BCF-2095
	// disable fkey checks until the end of the test transaction
	require.NoError(t, utils.JustError(
		app.GetDB().ExecContext(ctx, `SET CONSTRAINTS job_spec_errors_v2_job_id_fkey DEFERRED`)))

	var ocrSpec job.OCROracleSpec
	err = toml.Unmarshal([]byte(ocrspec.Toml()), &ocrSpec)
	require.NoError(t, err)
	jb.OCROracleSpec = &ocrSpec
	jb.OCROracleSpec.TransmitterAddress = &app.Keys[0].EIP55Address
	err = app.AddJobV2(ctx, &jb)
	require.NoError(t, err)
	dbJb, err := app.JobORM().FindJob(ctx, jb.ID)
	require.NoError(t, err)
	require.Equal(t, dbJb.Name.String, ocrspec.Name)

	// test Calling update on the job id with changed values should succeed.
	updatedSpec := testspecs.GenerateOCRSpec(testspecs.OCRSpecParams{
		DS1BridgeName:      bridge2.Name.String(),
		DS2BridgeName:      bridge.Name.String(),
		Name:               "updated OCR job",
		TransmitterAddress: app.Keys[0].Address.Hex(),
		EVMChainID:         cltest.FixtureChainID.String(),
	})
	require.NoError(t, err)
	body, _ := json.Marshal(web.UpdateJobRequest{
		TOML: updatedSpec.Toml(),
	})
	response, cleanup := client.Put("/v2/jobs/"+strconv.Itoa(int(jb.ID)), bytes.NewReader(body))
	t.Cleanup(cleanup)

	dbJb, err = app.JobORM().FindJob(ctx, jb.ID)
	require.NoError(t, err)
	require.Equal(t, dbJb.Name.String, updatedSpec.Name)

	cltest.AssertServerResponse(t, response, http.StatusOK)
}
```
