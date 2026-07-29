[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** universalClient/tss/dkls/sign.go (L54-56)
```go
	if len(messageHash) == 0 {
		return nil, fmt.Errorf("message hash required")
	}
```

**File:** universalClient/tss/dkls/sign.go (L150-164)
```go
// GetResult returns the result when finished.
func (s *signSession) GetResult() (*Result, error) {
	sig, err := session.DklsSignSessionFinish(s.handle)
	if err != nil {
		return nil, fmt.Errorf("failed to finish sign session: %w", err)
	}

	// Verify signature before returning
	verified, verifyErr := s.verifySignature(s.publicKey, sig, s.messageHash)
	if verifyErr != nil {
		return nil, fmt.Errorf("signature verification error: %w", verifyErr)
	}
	if !verified {
		return nil, fmt.Errorf("signature verification failed")
	}
```

**File:** universalClient/tss/dkls/sign.go (L182-191)
```go
func (s *signSession) verifySignature(publicKey, signature, messageHash []byte) (bool, error) {
	if len(publicKey) != 33 {
		return false, fmt.Errorf("public key must be 33 bytes (compressed), got %d bytes", len(publicKey))
	}
	if len(signature) != 64 && len(signature) != 65 {
		return false, fmt.Errorf("signature must be 64 or 65 bytes (r || s [|| recovery_id]), got %d bytes", len(signature))
	}
	if len(messageHash) != 32 {
		return false, fmt.Errorf("message hash must be 32 bytes, got %d bytes", len(messageHash))
	}
```
