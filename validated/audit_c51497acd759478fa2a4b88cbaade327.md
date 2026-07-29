[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** x/uexecutor/types/universal_account_id.go (L29-32)
```go
func (p UniversalAccountId) ValidateBasic() error {
	p.ChainNamespace = strings.TrimSpace(p.ChainNamespace)
	p.ChainId = strings.TrimSpace(p.ChainId)
	p.Owner = strings.TrimSpace(p.Owner)
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L19-20)
```go
	// Get Caip2Identifier for the universal account
	caip2Identifier := universalAccountId.GetCAIP2()
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L38-41)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}
```
