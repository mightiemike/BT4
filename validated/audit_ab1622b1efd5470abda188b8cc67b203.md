[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** universalClient/chains/svm/tx_builder.go (L881-889)
```go
		accounts = tb.buildWithdrawAndExecuteAccounts(
			relayerKeypair.PublicKey(),
			configPDA, vaultPDA, ceaAuthorityPDA, tssPDA, executedTxPDA,
			targetProgram,
			isNative, instructionID,
			recipientPubkey, mintPubkey,
			execAccounts,
			solana.PublicKey{}, solana.PublicKey{}, // direct route: None sentinels for stored_ix_data + store_refund_recipient
		)
```

**File:** universalClient/chains/svm/tx_builder.go (L898-902)
```go
		accounts = tb.buildRevertAccounts(
			configPDA, vaultPDA, feeVaultPDA, tssPDA, recipientPubkey,
			executedTxPDA, relayerKeypair.PublicKey(),
			isNative, mintPubkey,
		)
```

**File:** universalClient/chains/svm/tx_builder.go (L936-936)
```go
	needsRecipientATA := (instructionID == 1 && !isNative) || ((instructionID == 3 || instructionID == 4) && !isNative)
```

**File:** universalClient/chains/svm/tx_builder.go (L1241-1241)
```go
	needsRecipientATA := !isNative && false // execute mode (id=2) doesn't create recipient ATA; gateway handles cea_ata internally
```

**File:** universalClient/chains/svm/tx_builder.go (L1347-1364)
```go
func (tb *TxBuilder) determineInstructionID(txType uetypes.TxType) (uint8, error) {
	switch txType {
	case uetypes.TxType_FUNDS:
		return 1, nil

	case uetypes.TxType_FUNDS_AND_PAYLOAD, uetypes.TxType_GAS_AND_PAYLOAD:
		return 2, nil

	case uetypes.TxType_INBOUND_REVERT:
		return 3, nil

	case uetypes.TxType_RESCUE_FUNDS:
		return 4, nil

	default:
		return 0, fmt.Errorf("unsupported tx type for SVM: %s", txType.String())
	}
}
```
