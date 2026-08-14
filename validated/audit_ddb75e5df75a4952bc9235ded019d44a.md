### Title
Sign counter (Authenticator.SignCount) is never persisted after WebAuthn login, enabling replay/clone detection bypass - (File: core/sessions/webauthn.go)

### Summary
`FinishWebAuthnLogin` calls `webAuthn.ValidateLogin(waUser, sessionData, credential)` and discards the returned updated credential via `_, err = webAuthn.ValidateLogin(...)`. Because the updated `SignCount` is never written back to storage, the persisted `WebAuthn.PublicKeyData` (and thus the `SignCount` compared on the next login) never advances, defeating the FIDO2/WebAuthn clone-detection mechanism.

### Finding Description
`FinishWebAuthnLogin` in `core/sessions/webauthn.go` builds the `WebAuthnUser` from the DB-stored credentials (`uwas`) via `duoWebAuthUserFromUser`, then calls: [1](#0-0) 
`webAuthn.ValidateLogin` internally checks that the assertion's `SignCount` is greater than the stored `Authenticator.SignCount` to detect cloned authenticators, and returns an updated `webauthn.Credential` reflecting the new sign count. This return value is discarded (`_`), and the function only returns `err`.

The only place in the codebase that writes a `WebAuthn` credential to storage is `AddCredentialToUser`, which calls `ap.SaveWebAuthn(ctx, &token)`: [2](#0-1) 
This function is used only during registration (`FinishWebAuthnRegistration` flow), not after login. Searching the codebase for `SaveWebAuthn`/`UpdateWebAuthn` callers confirms there is no post-login update path — the ORM's `CreateSession` (`core/sessions/localauth/orm.go`) calls `sessions.FinishWebAuthnLogin(user, uwas, sr)` and, on success, simply creates a session record; it never re-persists the credential or its `SignCount`: [3](#0-2) 

Consequently, the `SignCount` stored in the `web_authns` table's `public_key_data` column is frozen at whatever value was set at registration (or effectively never advances), regardless of how many successful logins occur.

### Impact Explanation
WebAuthn's sign-counter mechanism exists specifically so that if an attacker clones an authenticator's private key material (e.g., via malware, firmware extraction, or a compromised authenticator implementation) and later replays a previously-observed assertion (or produces assertions from the cloned device), the relying party can detect the mismatch (the counter would not have advanced monotonically as expected) and lock/flag the account. Since Chainlink never persists the updated `SignCount`, this detection is completely inert — a cloned authenticator (or any captured/replayed valid assertion whose challenge-binding is otherwise satisfied) can authenticate indefinitely without triggering any clone/replay alarm. This weakens the assurance of the 2FA/MFA layer protecting the Operator UI login, an authentication bypass/detection-evasion issue.

### Likelihood Explanation
Exploitation requires an attacker to have already obtained a way to produce (or replay) a valid signed assertion for a victim's registered authenticator — e.g. via authenticator cloning, private key extraction, or another vulnerability enabling assertion replay — which is a real-world scenario the WebAuthn spec's sign counter is designed to catch. The vulnerability here is not that authentication itself is bypassed outright (a fresh `Challenge`/origin binding still must be satisfied per WebAuthn protocol checks inside `ValidateLogin`), but that Chainlink permanently disables the *detection* mechanism meant to catch such cloning/replay once it happens, since the stored `SignCount` never advances. This is a persistent, code-level defect (not a one-off misconfiguration), so it is deterministically reproducible on every login.

### Recommendation
After a successful `webAuthn.ValidateLogin` call, capture the returned updated `*webauthn.Credential`, and persist it back to storage (e.g., add an `UpdateWebAuthn`/equivalent method to `AuthenticationProvider` and call it from `FinishWebAuthnLogin` or from the caller in `core/sessions/localauth/orm.go`) so the new `SignCount` is written to the `web_authns.public_key_data` column, restoring clone-detection functionality.

### Proof of Concept
Unit/integration test plan (in `core/sessions/localauth/orm_test.go` or `core/sessions/webauthn_test.go`):
1. Register a WebAuthn credential for a test user with `SignCount = 0` via `AddCredentialToUser`.
2. Perform a successful `FinishWebAuthnLogin` with a crafted assertion whose `SignCount = 5` (using a test authenticator/mock that satisfies `ValidateLogin`).
3. Immediately after, call `orm.GetUserWebAuthn(ctx, email)` and unmarshal `PublicKeyData` into a `webauthn.Credential`.
4. Assert that `credential.Authenticator.SignCount == 5`. Currently this assertion fails because the stored value remains `0` (or whatever it was at registration), proving the sign counter is never persisted, so a later replayed assertion with the same or lower `SignCount` would still be treated as unseen/valid by any clone-check logic that relies on the persisted value.

### Citations

**File:** core/sessions/webauthn.go (L160-161)
```go
	_, err = webAuthn.ValidateLogin(waUser, sessionData, credential)
	return err
```

**File:** core/sessions/webauthn.go (L283-294)
```go
func AddCredentialToUser(ctx context.Context, ap AuthenticationProvider, email string, credential *webauthn.Credential) error {
	credj, err := json.Marshal(credential)
	if err != nil {
		return err
	}

	token := WebAuthn{
		Email:         email,
		PublicKeyData: sqlxTypes.JSONText(credj),
	}
	return ap.SaveWebAuthn(ctx, &token)
}
```

**File:** core/sessions/localauth/orm.go (L201-219)
```go
	// The user is at the final stage of logging in with MFA. We have an
	// attestation back from the user, we now need to verify that it is
	// correct.
	err = sessions.FinishWebAuthnLogin(user, uwas, sr)

	if err != nil {
		// The user does have WebAuthn enabled but failed the check
		o.auditLogger.Audit(audit.AuthLoginFailed2FA, map[string]any{"email": sr.Email, "error": err})
		lggr.Errorf("User sent an invalid attestation: %v", err)
		return "", pkgerrors.New("MFA Error")
	}

	lggr.Infof("User passed MFA authentication and login will proceed")
	// This is a success so we can create the sessions
	session := sessions.NewSession()
	_, err = o.ds.ExecContext(ctx, "INSERT INTO sessions (id, email, last_used, created_at) VALUES ($1, $2, now(), now())", session.ID, user.Email)
	if err != nil {
		return "", err
	}
```
