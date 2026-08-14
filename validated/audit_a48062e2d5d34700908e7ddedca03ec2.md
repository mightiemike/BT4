### Title
Missing curve/low-order-point validation for workflow-supplied X25519 encryption keys before use in `box.SealAnonymous` - ([File: core/services/ocr2/plugins/vault/plugin.go])

### Summary
The Vault OCR3 reporting plugin encrypts TDH2 decryption shares to a recipient public key supplied by the requesting workflow, but only checks the key's byte length before feeding it directly into NaCl `box.SealAnonymous`. No check is performed that the supplied bytes represent a valid, non-degenerate Curve25519 point (e.g., rejecting the identity element or known small-order points), mirroring the "accept arbitrary byte strings as public keys" bug class described in the external report.

### Finding Description
`share.encryptWithKeyBinary` in `core/services/ocr2/plugins/vault/plugin.go` decodes a hex-encoded public key supplied via `vaultcommon.SecretRequest.EncryptionKeys` (part of `GetSecretsRequest`), validates only that its length equals `curve25519.PointSize` (32 bytes), and then passes it straight to `box.SealAnonymous`: [1](#0-0) 

This key is invoked once per entry in `secretRequest.EncryptionKeys` inside `observeGetSecretsRequest`, which runs during the OCR3 `Observation` phase of the Vault reporting plugin — i.e., on the node itself, using attacker/workflow-supplied input: [2](#0-1) 

The `GetSecretsRequest.Requests[].EncryptionKeys` field is populated end-to-end from the workflow layer (`secretsFetcher` in `core/services/workflows/v2/secrets.go`), which is driven by user/workflow-owner-authored workflow code fetching secrets via the SDK. A malicious or buggy workflow can therefore supply a 32-byte value that decodes successfully but is not a valid, full-order Curve25519 point (e.g., the identity element or a small-order point), and no validation rejects it. The existing test suite (`TestPlugin_Observation_GetSecretsRequest_PublicKeyIsInvalid`) demonstrates only hex-decoding failures are caught — an arbitrary but correctly-sized byte string sails through unchecked: [3](#0-2) 

This is analogous to the report's core issue: public key material accepted from an untrusted party and used directly for ECIES/box-style encryption with no structural/curve validation beyond a length check, at the boundary between an unprivileged actor (the workflow author) and node-level cryptographic operations.

### Impact Explanation
If an attacker supplies a small-order or identity Curve25519 point as `EncryptionKeys`, the resulting shared secret in `box.SealAnonymous` may become predictable or degenerate (a form of the small-subgroup issue described in the source report). Because this is executed uniformly by every node participating in the OCR3 round during `Observation`, a malformed key could also cause divergent behavior across nodes (though `box.SealAnonymous`/curve25519 in Go's `x/crypto` does not itself error on such inputs, so this manifests as a silent weak-encryption condition rather than a crash). The practical impact is a weakening of the confidentiality guarantee for the TDH2 decryption share being returned to the requester, and, more broadly, is evidence that node-side cryptographic input from unprivileged workflow authors is not held to the same validation bar recommended by the report (explicit `publicKeyVerify`-equivalent checks) before being used in encryption.

### Likelihood Explanation
Reaching this path only requires deploying a workflow that calls the secrets-fetching SDK method with a crafted 32-byte "encryption key" instead of a normal ephemeral/workflow key — something any workflow owner (an unprivileged, non-node-operator actor) can do without special privileges once they can register and run a workflow.

### Recommendation
Add explicit validation in `share.encryptWithKeyBinary` (or upstream in `observeGetSecretsRequest`) that the decoded 32-byte value is not the identity point and is not one of the well-known Curve25519 small-order points before calling `box.SealAnonymous`, consistent with the report's recommendation to centralize and enforce public-key structural validation at every network/request-ingestion boundary rather than relying solely on length checks.

### Proof of Concept
1. A workflow author writes a workflow that calls the `GetSecrets` SDK function but sets `EncryptionKeys` to a crafted 32-byte value known to be a small-order/identity Curve25519 point (rather than a value derived from `box.GenerateKey`).
2. The Vault capability request flows to the Vault OCR3 plugin's `Observation` phase, reaching `observeGetSecretsRequest` → `sh.encryptWithKeyBinary(pk)`.
3. Only `len(publicKey) != curve25519.PointSize` is checked; the malformed point passes and is used directly as the recipient key in `box.SealAnonymous`, producing ciphertext whose underlying shared secret is degenerate/predictable, without any error being raised to reject the request as in the case of a hex-decoding failure. [1](#0-0)

### Citations

**File:** core/services/ocr2/plugins/vault/plugin.go (L903-920)
```go
func (s *share) encryptWithKeyBinary(pk string) ([]byte, error) {
	publicKey, err := hex.DecodeString(pk)
	if err != nil {
		return nil, newUserError("failed to convert public key to bytes: " + err.Error())
	}

	if len(publicKey) != curve25519.PointSize {
		return nil, newUserError(fmt.Sprintf("invalid public key size: expected %d bytes, got %d bytes", curve25519.PointSize, len(publicKey)))
	}

	publicKeyLength := [curve25519.PointSize]byte(publicKey)
	encrypted, err := box.SealAnonymous(nil, s.data, &publicKeyLength, rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("failed to encrypt decryption share: %w", err)
	}

	return encrypted, nil
}
```

**File:** core/services/ocr2/plugins/vault/plugin.go (L948-993)
```go
func (r *ReportingPlugin) observeGetSecretsRequest(ctx context.Context, reader ReadKVStore, secretRequest *vaultcommon.SecretRequest, requestsCountForID map[string]int) (*vaultcommon.SecretResponse, error) {
	id, err := r.validateSecretIdentifier(ctx, secretRequest.Id)
	if err != nil {
		return nil, err
	}

	if requestsCountForID[vaulttypes.KeyFor(secretRequest.Id)] > 1 {
		return nil, newUserError("duplicate request for secret identifier " + vaulttypes.KeyFor(id))
	}

	secret, err := reader.GetSecret(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("failed to read secret from key-value store: %w", err)
	}
	if secret == nil {
		return nil, newUserError("key does not exist")
	}

	sh, err := generatePlaintextShare(r.cfg.PublicKey, r.cfg.PrivateKeyShare, secret.EncryptedSecret, id.Owner)
	if err != nil {
		return nil, err
	}

	shares := []*vaultcommon.EncryptedShares{}
	useBinaryShares := r.optimizationsEnabled(ctx)
	for _, pk := range secretRequest.EncryptionKeys {
		encShare, err := sh.encryptWithKeyBinary(pk)
		if err != nil {
			return nil, err
		}

		if useBinaryShares {
			shares = append(shares, &vaultcommon.EncryptedShares{
				EncryptionKey: pk,
				BinaryShares:  [][]byte{encShare},
			})
		} else {
			shares = append(shares, &vaultcommon.EncryptedShares{
				EncryptionKey: pk,
				Shares: []string{
					hex.EncodeToString(encShare),
				},
			})
		}
	}

```

**File:** core/services/ocr2/plugins/vault/plugin_test.go (L1339-1401)
```go
func TestPlugin_Observation_GetSecretsRequest_PublicKeyIsInvalid(t *testing.T) {
	_, pk, shares, err := tdh2easy.GenerateKeys(1, 3)
	require.NoError(t, err)
	r := newTestReportingPlugin(t, withKeys(pk, shares[0]))

	id := &vaultcommon.SecretIdentifier{
		Owner:     "owner",
		Namespace: "main",
		Key:       "my_secret",
	}
	rdr := &kv{
		m: make(map[string]response),
	}

	plaintext := []byte("my-secret-value")
	ciphertext, err := tdh2easy.Encrypt(pk, plaintext)
	require.NoError(t, err)
	ciphertextBytes, err := ciphertext.Marshal()
	require.NoError(t, err)

	err = newTestWriteStore(t, rdr).WriteSecret(t.Context(), id, &vaultcommon.StoredSecret{
		EncryptedSecret: ciphertextBytes,
	})
	require.NoError(t, err)

	p := &vaultcommon.GetSecretsRequest{
		Requests: []*vaultcommon.SecretRequest{
			{
				Id:             id,
				EncryptionKeys: []string{"foo"},
			},
		},
	}
	anyp, err := anypb.New(p)
	require.NoError(t, err)
	err = newTestWriteStore(t, rdr).WritePendingQueue(t.Context(),
		[]*vaultcommon.StoredPendingQueueItem{
			{Id: "request-1", Item: anyp},
		},
	)
	require.NoError(t, err)
	seqNr := uint64(1)
	data, err := r.Observation(t.Context(), seqNr, types.AttributedQuery{}, rdr, &blobber{})
	require.NoError(t, err)

	obs := &vaultcommon.Observations{}
	err = proto.Unmarshal(data, obs)
	require.NoError(t, err)

	assert.Len(t, obs.Observations, 1)
	o := obs.Observations[0]

	assert.Equal(t, vaultcommon.RequestType_GET_SECRETS, o.RequestType)
	assert.True(t, proto.Equal(p, o.GetGetSecretsRequest()))

	batchResp := o.GetGetSecretsResponse()
	assert.Len(t, p.Requests, 1)
	assert.Len(t, p.Requests, len(batchResp.Responses))

	assert.True(t, proto.Equal(p.Requests[0].Id, batchResp.Responses[0].Id))
	resp := batchResp.Responses[0]

	assert.Contains(t, resp.GetError(), "failed to convert public key to bytes")
```
