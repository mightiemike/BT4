## Analysis: Relayer identity not bound in TSS-signed message allows fee-reimbursement hijack

### Title
Malicious Relayer Can Hijack Gas-Fee Reimbursement by Substituting Themselves as the SVM Outbound `caller` — ([File: universalClient/chains/svm/tx_builder.go])

### Summary
On the Solana outbound path, the TSS group signs a message that authorizes the cross-chain operation, but that signed message does not bind the identity of the relayer/fee-payer (`caller`) who submits the transaction and is reimbursed the `gasFee` from the vault. Any actor who obtains the TSS signature can build and broadcast their own transaction naming themselves as `caller`, diverting the gas-fee reimbursement away from the relayer selected by the coordinator — the same bug class as Connext's router-swapping issue, where a fee-earning identity was not committed inside signed data.

### Finding Description
`GetOutboundSigningRequest` builds the message that TSS validators sign via `constructTSSMessage`, covering `instructionID, chainID, deadline, amount, txID, universalTxID, sender, token, gasFee, targetProgram, accounts, ixData, revertRecipient, revertMint, revertMsg` [1](#0-0) . The `gasFee` amount is committed, but the *recipient* of that reimbursement — the relayer/`caller` account — is never part of the signed payload.

When building the actual Solana transaction, the relayer's local keypair is loaded from disk and used purely as the local fee-payer/signer, independent of anything TSS signed [2](#0-1) . That keypair's public key is inserted as the writable, signing `caller` account in the instruction's accounts list [3](#0-2) , and for revert/rescue flows the `caller` is also the recipient of `fee_vault` reimbursement [4](#0-3) . The code and doc comments confirm gas is reimbursed to "the relayer" from the pre-baked `gasFee`, but which relayer receives it is not cryptographically bound: "the actual gas cost is the base fee + PDA rent paid by the relayer, which is reimbursed from the gasFee baked into the signed message" [5](#0-4) .

Because every honest Universal Validator independently re-derives the same signing request and broadcasts an "identical signed transaction" where "the first to land wins, the rest are idempotent" [6](#0-5) , the TSS signature over `(instructionID, ..., gasFee, ...)` is effectively public/reconstructible information once produced. Nothing prevents a third party — including any relayer running non-coordinator-selected code, or anyone who intercepts the broadcast transaction and race-front-runs it with their own `caller` substituted — from re-assembling the same instruction data (unchanged, since it doesn't reference `caller`) with themselves as the fee-payer/signer, and winning the SOL fee reimbursement race instead of the intended relayer.

This mirrors the Connext bug precisely: the protocol validates an internally-consistent, cryptographically-authorized payload (TSS signature / router signature) but never binds *who* is allowed to claim the associated fee, so an opportunistic actor swaps themselves into the fee-earning slot without needing to forge anything.

### Impact Explanation
This lets an unprivileged actor divert SOL-denominated gas-fee reimbursement (`fee_vault` payout) intended for the relayer that actually incurred the on-chain execution cost, to themselves, by racing to submit the reconstructed transaction first. It does not let an attacker steal user principal or forge TSS authorization — the underlying gateway operation (withdraw/execute/revert amount, recipient, payload) is unchanged and TSS-authorized regardless of who is `caller`. The material impact is limited to redirection of the relayer's fee-vault reimbursement, i.e. loss of the intended reward to an unprivileged front-runner, not double-spend, and not user fund loss.

### Likelihood Explanation
Likelihood is constrained by the fact that any relayer in the Universal Validator set is expected to broadcast the identical transaction as soon as the TSS signature is produced, so multiple honest broadcasts already race each other by design ("the first to land wins") [6](#0-5) ; an unprivileged outsider would additionally need to observe/derive the TSS-signed hash and outrace the legitimate broadcaster(s), which requires network-level positioning rather than a purely on-chain trigger. This weakens applicability to the "no network-level DoS/race" boundary implied by the allowed-impact gate, and the report should be read with that caveat.

### Recommendation
Bind the intended fee-payer/reimbursement-recipient identity into the TSS-signed message (e.g., include a `caller`/`relayer` field selected by the rotating coordinator, or make the fee-vault reimbursement recipient a value chosen and verified against a committed on-chain assignment) so that only the intended relayer can claim the gas-fee reimbursement for a given TSS-authorized outbound.

### Proof of Concept
Conceptual, since it requires observing broadcast TSS signatures off-chain (not fully reproducible purely from repository code):
1. Coordinator selects relayer A for an SVM outbound; TSS nodes sign `messageHash` covering `(instructionID, chainID, deadline, amount, txID, universalTxID, sender, token, gasFee, targetProgram, accounts, ixData, ...)` — no `caller` field.
2. Once the signature is available (e.g., observed on the P2P layer, mempool, or from a partially-propagated tx), attacker B independently calls the equivalent of `BuildOutboundTransaction` using their own keypair as `caller`/fee-payer, since instruction data does not depend on `caller`.
3. B broadcasts first; the gateway program's `executed_sub_tx` replay-guard accepts the first-landed transaction and pays the `gasFee` reimbursement to B's account instead of A's, per the `fee_vault`/`caller` wiring in `buildWithdrawAndExecuteAccounts`/`buildRevertAccounts`.

Note: I could not verify from the indexed code whether the on-chain Anchor gateway program (not in this repo — likely `push-chain-core-contracts` or a separate Solana program repo) imposes any additional `caller`-binding check at the program level; this analysis is based solely on the Go-side `TxBuilder` construction logic in `universalClient/chains/svm/tx_builder.go`. If the on-chain program independently restricts `caller` to a whitelisted/committed relayer set, this finding would be mitigated — that check is outside the scope of what is indexed here.

### Citations

**File:** universalClient/chains/svm/tx_builder.go (L395-411)
```go
	// --- Construct the TSS message and hash it ---
	// This message is what TSS validators sign. The gateway contract reconstructs
	// the same message on-chain and verifies the signature matches.
	messageHash, err := tb.constructTSSMessage(
		instructionID, chainID, data.SigningDeadline, amount.Uint64(),
		txID, universalTxID, sender, token, gasFee,
		targetProgram, accounts, ixData,
		revertRecipient, revertMint, revertMsg,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to construct TSS message: %w", err)
	}

	return &common.UnsignedSigningReq{
		SigningHash: messageHash, // This is the keccak256 hash to be signed by TSS
		Nonce:       nonce,
	}, nil
```

**File:** universalClient/chains/svm/tx_builder.go (L461-466)
```go
// GetGasFeeUsed returns "0" for SVM. SVM gas accounting is handled via vault
// gasFee reimbursement — the actual gas cost is the base fee + PDA rent paid
// by the relayer, which is reimbursed from the gasFee baked into the signed message.
func (tb *TxBuilder) GetGasFeeUsed(ctx context.Context, txHash string) (string, error) {
	return "0", nil
}
```

**File:** universalClient/chains/svm/tx_builder.go (L689-696)
```go
	// Load the relayer's Solana keypair from disk.
	// The relayer is the entity that pays for Solana transaction fees (gas).
	// Its Ed25519 signature authorizes the Solana transaction itself.
	// (This is separate from the TSS secp256k1 signature that authorizes the cross-chain operation.)
	relayerKeypair, err := tb.loadRelayerKeypair()
	if err != nil {
		return nil, 0, fmt.Errorf("failed to load relayer keypair: %w", err)
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L1938-1948)
```go
	// First 8 required accounts (always present)
	accounts := []*solana.AccountMeta{
		{PublicKey: caller, IsWritable: true, IsSigner: true},
		{PublicKey: configPDA, IsWritable: false, IsSigner: false},
		{PublicKey: vaultPDA, IsWritable: true, IsSigner: false},
		{PublicKey: ceaAuthorityPDA, IsWritable: true, IsSigner: false},
		{PublicKey: tssPDA, IsWritable: true, IsSigner: false},
		{PublicKey: executedTxPDA, IsWritable: true, IsSigner: false},
		{PublicKey: solana.SystemProgramID, IsWritable: false, IsSigner: false},
		{PublicKey: destinationProgram, IsWritable: false, IsSigner: false},
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L2071-2091)
```go
func (tb *TxBuilder) buildRevertAccounts(
	configPDA solana.PublicKey,
	vaultPDA solana.PublicKey,
	feeVaultPDA solana.PublicKey,
	tssPDA solana.PublicKey,
	recipient solana.PublicKey,
	executedTxPDA solana.PublicKey,
	caller solana.PublicKey,
	isNative bool,
	mintPubkey solana.PublicKey,
) []*solana.AccountMeta {
	accounts := []*solana.AccountMeta{
		{PublicKey: configPDA, IsWritable: false, IsSigner: false},
		{PublicKey: vaultPDA, IsWritable: true, IsSigner: false},
		{PublicKey: feeVaultPDA, IsWritable: true, IsSigner: false},
		{PublicKey: tssPDA, IsWritable: true, IsSigner: false},
		{PublicKey: recipient, IsWritable: true, IsSigner: false},
		{PublicKey: executedTxPDA, IsWritable: true, IsSigner: false},
		{PublicKey: caller, IsWritable: true, IsSigner: true},
		{PublicKey: solana.SystemProgramID, IsWritable: false, IsSigner: false},
	}
```

**File:** universalClient/README.md (L107-112)
```markdown
1. The Push Chain listener picks up the pending outbound
2. A rotating coordinator assigns a nonce, selects a threshold subset of participants, and creates a DKLS signing session
3. Each participant independently verifies the signing request against their own RPC view of the destination chain, then collaborates in the distributed signing protocol
4. Every participating validator broadcasts the identical signed transaction; the first to land wins, the rest are idempotent (same nonce, same signature, same tx hash)
5. The resolver monitors the destination chain for confirmation
6. On success, the event is marked complete. On failure (reverted or not found after retries), validators vote failure on Push Chain, which triggers a refund to the user
```
