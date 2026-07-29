### Title
Fund-migration signing hash uses a live, attacker-inflatable balance query, causing honest-validator hash mismatch and DoS of TSS key-rotation fund recovery - ([File: universalClient/chains/evm/tx_builder.go])

### Summary
Similar to the Aave `_coverDeficit()` bug — where `balanceOf(address(this))` is used instead of the actually-transferred amount, letting an attacker inflate a balance-dependent computation via a direct donation — Push Chain's fund-migration flow computes the amount to sweep from the old TSS address using a **live, independently-queried on-chain balance** rather than a value fixed once and shared deterministically across all validators. An unprivileged attacker can send a dust native-token transfer to the (publicly known) old TSS address at the right moment to make different honest validators observe different balances, producing different signing hashes and breaking TSS threshold signing consensus for the migration.

### Finding Description
`InitiateFundMigration` (admin-triggered) emits a public `FundMigrationInitiatedEvent` containing `OldTssPubkey` [1](#0-0) , from which any observer can derive the old TSS EVM address (`DeriveEVMAddressFromPubkey`) [2](#0-1) . This address is a plain EOA that receives arbitrary sends from anyone — an unprivileged attacker.

The coordinator builds the signing request for the migration sweep by calling `buildFundMigrationTransaction` with `claimedAmount = nil`, meaning the balance is queried live from the chain via `builder.GetFundMigrationSigningRequest` → `tb.rpcClient.GetBalance(ctx, fromAddr)`: [3](#0-2) [4](#0-3) 

The transfer amount is then `balance - gasCost - l1GasFee` (`computeFundMigrationTransfer`) and is baked into the EIP-155 signing hash used for the threshold signature — every validator's independently computed hash must match exactly: [5](#0-4) 

Critically, each verifying (non-coordinator) validator independently re-derives this same signing request in `verifyFundMigrationSigningRequest`, and it also does **not** pass `Balance` in `FundMigrationData` — it re-queries the live chain balance itself: [6](#0-5) 

Because the coordinator and each verifying validator query `GetBalance` at different wall-clock/block times (network latency, poll intervals, block production), an attacker who sends even 1 wei to the old TSS address between these queries will cause some validators to observe a higher balance than others. This produces divergent `maxTransfer` values and therefore divergent `SigningHash` values among **honest** validators — exactly the "wrong balance" leading to "wrong TSS event" scenario called out in the task's audit pivots. The code comment even acknowledges the balance-mutability risk ("used by the ACK verify path to rebuild the hash deterministically without racing a successful sweep") but that reconstruction (`claimedAmount`) is only used in one specific ACK-verification branch, not in the initial coordinator-vs-verifier hash agreement path exercised by `verifyFundMigrationSigningRequest`.

### Impact Explanation
This is a DoS on the honest-validator finalization path for TSS fund migration: a hash mismatch causes `verifyFundMigrationSigningRequest` to return an error and the validator to refuse to sign, breaking the DKLS threshold-signing session that requires all participants to sign the same hash. An attacker can repeatedly front-run the old-TSS-address balance query with dust transfers, indefinitely stalling the fund-migration sweep that recovers funds from a rotated-out TSS key. Since fund migration is the mechanism for pulling all remaining chain funds out of a decommissioned TSS vault after a key rotation, prolonged DoS keeps protocol funds parked in the deprecated key's address, which is an availability/asset-recovery risk (not permanent loss, but indefinite freezing of the sweep as long as the attacker keeps griefing).

### Likelihood Explanation
Likelihood is moderate-to-high: the trigger requires only a trivial, unprivileged native-token transfer to a publicly derivable address, timed around a public on-chain `FundMigrationInitiatedEvent`. No privileged access, validator collusion, or protocol knowledge beyond public event data is needed. The attacker only needs to repeat the dust transfer for each migration attempt to keep the session from reaching quorum on a shared hash.

### Recommendation
Fix the balance-basis for the migration sweep the same way the report recommends for `_coverDeficit()`: fix the balance value once (e.g., at coordinator sign-setup time) and thread that exact value through to every verifying validator instead of having each validator re-query the chain independently. The migration record (`FundMigration`/`FundMigrationInitiatedEventData`) or the coordinator's `UnsignedSigningReq` should carry the balance/amount, and `verifyFundMigrationSigningRequest` should use `claimedAmount`/the shared value (as the ACK path already does) rather than calling `GetBalance` again. This removes the race window an attacker can exploit via direct token transfers to the vault address.

### Proof of Concept
1. Admin calls `MsgInitiateFundMigration`, emitting `FundMigrationInitiatedEvent` with `OldTssPubkey` publicly on-chain.
2. Attacker derives `oldTSSAddr = DeriveEVMAddressFromPubkey(OldTssPubkey)` from the public event and sends a 1-wei native transfer to it right as coordinator/validators begin signing.
3. Coordinator calls `createFundMigrationSignSetup` → `buildFundMigrationTransaction(..., claimedAmount=nil)` → `GetFundMigrationSigningRequest` queries `GetBalance(oldTSSAddr)` at block N, computing `SigningHash_A`.
4. A verifying validator's `verifyFundMigrationSigningRequest` queries `GetBalance(oldTSSAddr)` slightly later at block N+1 (after attacker's transfer lands), computing `SigningHash_B ≠ SigningHash_A`.
5. `bytes.Equal(signingReq.SigningHash, req.SigningHash)` fails, verifying validator rejects, DKLS signing session cannot reach threshold — migration stalls. Attacker repeats on retry to sustain the DoS.

Note: I could not fully trace every retry/backoff path in the migration workflow (e.g., how many retries the coordinator makes and whether a cooldown eventually forces a shared-balance reconstruction on all subsequent attempts), so the exact persistence/duration of this DoS across retries is not fully confirmed from the available code.

### Citations

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L112-128)
```go
	// 8. Emit event
	event, err := types.NewFundMigrationInitiatedEvent(types.FundMigrationInitiatedEventData{
		MigrationID:      migrationId,
		OldKeyID:         oldKeyId,
		OldTssPubkey:     oldKey.TssPubkey,
		CurrentKeyID:     currentKey.KeyId,
		CurrentTssPubkey: currentKey.TssPubkey,
		Chain:            chain,
		BlockHeight:      sdkCtx.BlockHeight(),
		GasPrice:         gasPrice.String(),
		GasLimit:         gasLimit,
		L1GasFee:         l1GasFee.String(),
	})
	if err != nil {
		return 0, fmt.Errorf("failed to create migration event: %w", err)
	}
	sdkCtx.EventManager().EmitEvent(event)
```

**File:** universalClient/tss/coordinator/coordinator.go (L588-613)
```go
	// Load old keyshare as a sanity check; keyID bytes are derived from the string.
	if _, err := c.keyshareManager.Get(migrationData.OldKeyID); err != nil {
		return nil, nil, fmt.Errorf("failed to load keyshare for old keyId %s: %w", migrationData.OldKeyID, err)
	}
	keyIDBytes := deriveKeyIDBytes(migrationData.OldKeyID)

	signingReq, err := c.buildFundMigrationTransaction(ctx, eventData, assignedNonce, nil /* query chain for balance */)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to build fund migration transaction: %w", err)
	}

	participantIDs := make([]byte, 0, len(partyIDs)*10)
	for i, partyID := range partyIDs {
		if i > 0 {
			participantIDs = append(participantIDs, 0)
		}
		participantIDs = append(participantIDs, []byte(partyID)...)
	}

	setupData, err := session.DklsSignSetupMsgNew(keyIDBytes, nil, signingReq.SigningHash, participantIDs)
	if err != nil {
		return nil, nil, fmt.Errorf("failed to create fund migration sign setup: %w", err)
	}

	return setupData, signingReq, nil
}
```

**File:** universalClient/tss/coordinator/coordinator.go (L628-631)
```go
	oldTSSAddr, err := DeriveEVMAddressFromPubkey(migrationData.OldTssPubkey)
	if err != nil {
		return nil, fmt.Errorf("derive old TSS address: %w", err)
	}
```

**File:** universalClient/chains/evm/tx_builder.go (L493-507)
```go
	var balance *big.Int
	if data.Balance != nil {
		balance = new(big.Int).Set(data.Balance)
	} else {
		queried, err := tb.rpcClient.GetBalance(ctx, fromAddr)
		if err != nil {
			return nil, fmt.Errorf("failed to get balance of %s: %w", data.From, err)
		}
		balance = queried
	}

	maxTransfer, err := computeFundMigrationTransfer(balance, data.GasPrice, data.GasLimit, data.L1GasFee)
	if err != nil {
		return nil, err
	}
```

**File:** universalClient/chains/evm/tx_builder.go (L590-607)
```go
// computeFundMigrationTransfer returns the native amount to sweep from the old
// TSS address to the new one: balance - (gasPrice * gasLimit) - l1GasFee.
// The l1GasFee covers OP-stack sequencer data-availability charges (0 for
// non-L2 chains). All validators must compute the same value — any drift
// here breaks the TSS signing hash.
func computeFundMigrationTransfer(balance, gasPrice *big.Int, gasLimit uint64, l1GasFee *big.Int) (*big.Int, error) {
	gasCost := new(big.Int).Mul(gasPrice, new(big.Int).SetUint64(gasLimit))
	totalFee := new(big.Int).Set(gasCost)
	if l1GasFee != nil && l1GasFee.Sign() > 0 {
		totalFee.Add(totalFee, l1GasFee)
	}
	maxTransfer := new(big.Int).Sub(balance, totalFee)
	if maxTransfer.Sign() <= 0 {
		return nil, fmt.Errorf("insufficient balance for gas: balance=%s gasCost=%s l1GasFee=%s",
			balance.String(), gasCost.String(), l1GasFeeString(l1GasFee))
	}
	return maxTransfer, nil
}
```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L1032-1054)
```go
	// Rebuild fund migration signing request with coordinator's nonce.
	// Parsing must match what the coordinator did; otherwise the reconstructed
	// hash on OP-stack chains diverges and the verification below rejects it.
	gasPrice := new(big.Int)
	gasPrice.SetString(migrationData.GasPrice, 10)

	l1GasFee := new(big.Int)
	l1GasFee.SetString(migrationData.L1GasFee, 10)

	migrationFundData := &common.FundMigrationData{
		From:     oldTSSAddr,
		To:       currentTSSAddr,
		GasPrice: gasPrice,
		GasLimit: migrationData.GasLimit,
		L1GasFee: l1GasFee,
	}
	signingReq, err := builder.GetFundMigrationSigningRequest(ctx, migrationFundData, req.Nonce)
	if err != nil {
		return fmt.Errorf("failed to get fund migration signing request for verification: %w", err)
	}

	// Compare hashes - must match exactly
	if !bytes.Equal(signingReq.SigningHash, req.SigningHash) {
```
