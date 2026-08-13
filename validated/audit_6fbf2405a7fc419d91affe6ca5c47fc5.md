### Title
Raw private key export requires no re-authentication beyond an existing admin session — attacker-chosen `newpassword` re-encrypts the key without the original keystore master password ([File: core/services/keystore/eth.go])

### Summary
The Chainlink node exposes `POST /v2/keys/eth/export/:address` (and equivalent endpoints for `evm`, `p2p`, `ocr`, `ocr2`, `csa`, `vrf`, `solana`, `cosmos`, etc.) which decrypts the already-unlocked in-memory private key and re-encrypts it with an attacker-supplied `newpassword` query parameter. The only gate is `auth.RequiresAdminRole`, i.e., possession of a valid admin session cookie or API token — there is no re-prompt for the original keystore/master password before the raw key material is decrypted and handed back as an exportable, attacker-controlled-password keyfile. This mirrors the reported "Sign & Verify" wallet bug class: once a session/wallet is "unlocked" (here, an authenticated admin session), sensitive key material can be extracted without any additional authentication step.

### Finding Description
The route registration ties key export solely to role-based session/token authentication: [1](#0-0) 

`RequiresAdminRole` only checks that the current session's `User.Role == UserRoleAdmin`; it performs no secondary verification (e.g., re-entry of the keystore master password, MFA challenge, or WebAuthn step-up) before permitting operations that expose raw key material: [2](#0-1) 

The controller passes the caller-supplied `newpassword` straight through to the keystore's `Export`, which reads the key from the in-memory keyring (already decrypted/unlocked at node startup) and re-encrypts it with that caller-chosen password: [3](#0-2) [4](#0-3) 

Critically, `Export` never validates the caller against the original key-encryption password (the node's master password/`ks.masterKey`); it only checks `ks.isLocked()`. Any admin-role session/API-token holder can therefore obtain a fully working, self-chosen-password-encrypted export of every EVM/OCR/OCR2/P2P/CSA/VRF/Solana/etc. private key in the node's keystore, which is trivially decryptable by the attacker since they picked the password. The same pattern is repeated across all key types (`core/services/keystore/p2p.go:118-129`, `core/services/keystore/starknet.go:112-123`, `core/services/keystore/tron.go:113-124`, `core/services/keystore/vrf.go:117-128`, `core/services/keystore/cosmos.go:109-120`, `core/services/keystore/aptos.go:113-124`, `core/services/keystore/workflow.go:122-133`).

This is directly analogous to the reported bug: the "wallet" (node keystore) is effectively "unlocked" for the lifetime of any valid admin session, and revealing/exporting the raw private key requires no additional authentication step beyond that pre-existing session — exactly the "reveal secret key without owner's presence/extra auth" pattern described in the report.

### Impact Explanation
Compromise of an admin session cookie or API token (e.g., via XSS, CSRF misconfiguration, stolen browser session, leaked API credentials, or an insider with transient admin access) allows immediate, silent extraction of every node private key (EVM transmitter keys, OCR/OCR2 signing keys, P2P keys, CSA keys, VRF keys) with a password of the attacker's choosing. This enables full node impersonation, transaction signing, OCR report forgery, and complete compromise of the node's on-chain identity — a direct node/secret-disclosure compromise, not merely a lower-severity issue.

### Likelihood Explanation
Likelihood is elevated because: (1) the operation requires no re-authentication beyond the standard session/token check already used for many other admin actions, so any session hijack or token leak immediately yields full key export capability; (2) `SessionTimeout` for the web UI can be configured very long (see test fixtures using `'999h0m0s'`), extending the exposure window; (3) the export endpoint accepts an arbitrary attacker-chosen `newpassword`, removing any need to know a pre-existing secret to make the exported key usable.

### Recommendation
Require step-up authentication before key export/decryption — e.g., re-prompt for the account/session password (or require a fresh, short-lived re-authentication token) at the `Export` endpoints, independent of the general admin-role session check. Consider requiring the caller to also supply the node's existing keystore master password (or a freshly issued short-TTL confirmation token from `/sessions`) before `ks.Export` is invoked, rather than relying solely on `RequiresAdminRole`. Add audit-log alerting and rate limiting specifically on key-export endpoints, and consider a mandatory timeout/re-auth for admin sessions before any `*/export/*` route is reachable.

### Proof of Concept
1. Obtain a valid admin session cookie or API key/secret pair for the Chainlink node's admin API (via any session-hijack vector, e.g. XSS in the operator UI, or a leaked/rotated-late API token).
2. Send `POST /v2/keys/eth/export/<address>?newpassword=attackerChosenPassword` with that session/token — no other secret is required (`core/web/eth_keys_controller.go:240-258` → `core/services/keystore/eth.go:235-246`).
3. Receive the fully decrypted-then-re-encrypted key JSON, encrypted only with `attackerChosenPassword`.
4. Decrypt locally with the known password to obtain the raw ECDSA private key, fully compromising the node's on-chain identity — with no additional authentication challenge ever presented, matching the reported "reveal secret key without additional authentication" bug class.

### Citations

**File:** core/web/router.go (L315-320)
```go
		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/auth/auth.go (L238-255)
```go
// RequiresAdminRole extracts the user object from the context, and asserts the user's role is 'admin'
func RequiresAdminRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role != clsessions.UserRoleAdmin {
			c.Abort()
			addForbiddenErrorHeaders(c, "admin", string(user.Role), user.Email)
			jsonAPIError(c, http.StatusForbidden, errors.New("Forbidden"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/eth_keys_controller.go (L240-258)
```go
func (ekc *ETHKeysController) Export(c *gin.Context) {
	defer ekc.app.GetLogger().ErrorIfFn(c.Request.Body.Close, "Error closing Export request body")

	id := c.Param("address")
	newPassword := c.Query("newpassword")

	bytes, err := ekc.app.GetKeyStore().Eth().Export(c.Request.Context(), id, newPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	ekc.app.GetAuditLogger().Audit(audit.KeyExported, map[string]any{
		"type": "ethereum",
		"id":   id,
	})

	c.Data(http.StatusOK, MediaType, bytes)
}
```

**File:** core/services/keystore/eth.go (L235-246)
```go
func (ks *eth) Export(ctx context.Context, id string, password string) ([]byte, error) {
	ks.lock.RLock()
	defer ks.lock.RUnlock()
	if ks.isLocked() {
		return nil, ErrLocked
	}
	key, err := ks.getByID(id)
	if err != nil {
		return nil, err
	}
	return key.ToEncryptedJSON(password, ks.scryptParams)
}
```
