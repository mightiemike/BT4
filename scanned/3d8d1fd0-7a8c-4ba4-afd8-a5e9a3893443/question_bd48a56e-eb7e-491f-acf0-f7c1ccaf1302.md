[File: 'File Name: x/uexecutor/types/keys.go -> Scope: Critical.'] [Symbol: GetOutboundRevertId/GetRescueFundsOutboundId] Do the 'REVERT' and 'RESCUE' domain suffixes appended as plain string literals inside the same colon-joined fmt.Sprintf (rather than being hashed as a separate field like InboundBallotDomain/Outbo

### Citations

**File:** x/uexecutor/types/keys.go (L69-78)
```go
// GetInboundUniversalTxKey: UTX identity from canonical (source_chain, tx_hash,
// log_index). Canonicalizes locals; caller's inbound is not mutated.
func GetInboundUniversalTxKey(inbound Inbound) string {
	chain := strings.TrimSpace(inbound.SourceChain)
	txHash := utils.LenientCanonicalizeTxHash(chain, inbound.TxHash)
	logIndex := strings.TrimSpace(inbound.LogIndex)
	data := fmt.Sprintf(
