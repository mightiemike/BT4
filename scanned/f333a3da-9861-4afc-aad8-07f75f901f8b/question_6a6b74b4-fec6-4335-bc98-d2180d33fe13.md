[File: 'universalClient/tss/txresolver/svm.go -> resolveSVM'] [Symbol: ReadSigningDeadline default] Because ReadSigningDeadline returns 0 for any event whose EventData is unparseable or lacks SigningDeadline, and resolveSVM only treats clusterTime==0 specially (not deadline==0), can an attacker-shaped event payload that fails json.Unmarshal in tx

### Citations

**File:** universalClient/tss/txresolver/svm.go (L60-73)
```go
	executed, clusterTime, err := builder.IsAlreadyExecuted(ctx, txID)
	if err != nil {
		log.Debug().Err(err).Msg(
