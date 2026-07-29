### Title
Unprivileged attacker can desynchronize TSS fund-migration signing hashes by front-running the old-vault balance query, permanently stalling fund migration - (File: `universalClient/chains/evm/tx_builder.go`)

### Summary
The external report's root cause is a "live balance" pattern: an account's *current* on-chain balance is read at distribution/settlement time and blindly treated as the authoritative "amount to move," even though the balance can be polluted by attacker-controlled deposits arriving between the intended snapshot and the actual read. Push Chain's TSS fund-migration flow uses the exact same anti-pattern: the amount swept from the old TSS vault is computed as `balance(oldTSSAddr) - gasCost - L1Fee` via a live RPC query, and this balance is independently re-queried by every Universal Validator at a different point in time to reconstruct/verify the coordinator's signing hash.

### Finding Description
`GetFundMigrationSigningRequest` in `universalClient/chains/evm/tx_builder.go` computes the migration transfer amount from a live queried balance when no `Balance` is explicitly supplied: [1](#0-0) 

```
var balance *big.Int
if data.Balance != nil {
    balance = new(big.Int).Set(data.Balance)
} else {
    queried, err := tb.rpcClient.GetBalance(ctx, fromAddr)
    ...
}
maxTransfer, err := computeFundMigrationTransfer(balance, data.GasPrice, data.GasLimit, data.L1GasFee)
```

The coordinator calls this with `claimedAmount == nil` when first building the setup message (`createFundMigrationSignSetup` → `buildFundMigrationTransaction`), so the coordinator's own signing hash is derived from *its own* live query of `oldTSSAddr`'s balance: [2](#0-1) 

Each Universal Validator independently reconstructs the same hash to verify the coordinator in `verifyFundMigrationSigningRequest`, and it also queries the balance live (no `Balance` field is populated in `migrationFundData`): [3](#0-2) 

Because `oldTSSAddr` (the deprecated TSS vault) is a public, well-known address (it's the previous TSS public key, discoverable from `TssKeyHistory`/on-chain events), any unprivileged external actor can send even a dust native-token transfer to it on the source chain at will. Since the coordinator and each of the N validators query this balance at different wall-clock times (network latency, node processing order), a single well-timed attacker transaction landing between any two of these independent RPC queries causes `computeFundMigrationTransfer` to yield a different `maxTransfer` for at least one participant, changing `tx.Value` and thus the computed `SigningHash`: [4](#0-3) 

A hash mismatch causes that validator to reject the setup: [5](#0-4) 

If enough honest validators reject due to (attacker-induced) mismatches, the DKLS MPC signing ceremony never produces a valid signature, so no fund-migration transaction is ever broadcast, and consequently no `MsgVoteFundMigration` (success or failure) is ever submitted on-chain. The `FundMigration` record therefore stays `PENDING` indefinitely. Because `InitiateFundMigration` explicitly rejects re-initiation while a pending migration exists for the same chain: [6](#0-5) 

the migration path for that chain becomes permanently stuck, and the funds held in the deprecated (old) TSS vault can never be swept to the current TSS vault through the normal protocol path.

### Impact Explanation
This blocks (freezes) protocol-controlled funds held under the old TSS key from ever being migrated to the current TSS key, since a duplicate-pending-migration guard prevents re-initiating while the attacker can repeat the griefing transaction indefinitely and cheaply (a single dust native transfer per attempt on the source chain). This matches the in-scope impact of "permanent freezing... of ... protocol-controlled funds" and "denial of service... not network-level... reachable without privileged control," since triggering it requires nothing more than an ordinary token send to a publicly known address — no relayer, validator, or admin privilege is needed.

### Likelihood Explanation
Likelihood is high for the DoS/griefing variant: the old TSS vault address is derivable from public `TssKeyHistory`/`FundMigrationInitiatedEvent` data, the attack requires only a trivial-value transaction sent at approximately the right time (a window that repeats on every retry attempt), and the attacker can simply keep sending small transfers whenever a `FUND_MIGRATION_STATUS_PENDING` record for the chain exists in order to keep desynchronizing balances between coordinator and verifiers.

### Recommendation
Do not derive the migration-sweep amount from a live-queried balance that any external unprivileged actor can influence at will. Snapshot the balance once (e.g., at a fixed source-chain block height/hash recorded in the `FundMigrationInitiatedEvent`) and have all participants query the *same* historical block instead of "latest," or have the coordinator's chosen amount included in the on-chain migration record so all UVs verify against that canonical value rather than independently re-querying a mutable balance. Additionally, consider treating "any extra balance beyond the pre-migration-canonical amount" as swept in a following migration round rather than blocking the current one, so dust/griefing transfers cannot indefinitely delay migration.

### Proof of Concept
1. Admin calls `InitiateFundMigration` for chain `C`, creating a `PENDING` `FundMigration` with public `OldTssPubkey` (hence a publicly derivable `oldTSSAddr` via `DeriveEVMAddressFromPubkey`).
2. The coordinator picks up the `FundMigrationInitiated` event, builds the setup message via `createFundMigrationSignSetup` → `buildFundMigrationTransaction(claimedAmount=nil)`, which queries `oldTSSAddr`'s current balance `B0` and computes `SigningHash0` from `maxTransfer0 = B0 - gasCost - L1Fee`.
3. An attacker, watching the mempool/chain for the (from the coordinator's own broadcast round) predictable timing of migration setup, sends a dust native transfer to `oldTSSAddr` right after the coordinator's query but before one or more validators call `verifyFundMigrationSigningRequest`.
4. Those validators query the now-changed balance `B1 = B0 + dust`, compute `maxTransfer1 ≠ maxTransfer0`, and thus `SigningHash1 ≠ SigningHash0`, causing `verifyFundMigrationSigningRequest` to return "fund migration signing hash mismatch."
5. If enough validators fail verification, the DKLS signing round cannot reach threshold; no signed transaction is produced; no `MsgVoteFundMigration` is ever submitted; the `FundMigration` record for chain `C` remains `PENDING` forever, and `InitiateFundMigration` rejects any retry due to the "pending migration already exists for chain" check.
6. The attacker repeats step 3 on every future attempt, permanently blocking migration of protocol funds out of the deprecated TSS vault for chain `C`.

### Citations

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

**File:** universalClient/tss/coordinator/coordinator.go (L580-613)
```go
// createFundMigrationSignSetup creates a sign setup message for fund migration.
// Uses the OLD key (not the current key) to sign a transaction moving funds from old TSS to current TSS.
func (c *Coordinator) createFundMigrationSignSetup(ctx context.Context, eventData []byte, partyIDs []string, assignedNonce *uint64) ([]byte, *common.UnsignedSigningReq, error) {
	var migrationData utsstypes.FundMigrationInitiatedEventData
	if err := json.Unmarshal(eventData, &migrationData); err != nil {
		return nil, nil, fmt.Errorf("failed to unmarshal fund migration event data: %w", err)
	}

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

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L1032-1051)
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
```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L1053-1061)
```go
	// Compare hashes - must match exactly
	if !bytes.Equal(signingReq.SigningHash, req.SigningHash) {
		sm.logger.Error().
			Str("our_hash", hex.EncodeToString(signingReq.SigningHash)).
			Str("coordinator_hash", hex.EncodeToString(req.SigningHash)).
			Str("event_id", event.EventID).
			Msg("fund migration signing hash mismatch - rejecting signing request")
		return fmt.Errorf("fund migration signing hash mismatch: our computed hash does not match coordinator's hash")
	}
```

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L49-63)
```go
	// 6. Check no existing PENDING migration for this chain
	err = k.PendingMigrations.Walk(ctx, nil, func(migrationId uint64, _ uint64) (bool, error) {
		m, err := k.FundMigrations.Get(ctx, migrationId)
		if err != nil {
			return true, err
		}
		if m.Chain == chain {
			return true, fmt.Errorf("pending migration already exists for chain %s (migration_id: %d, old_key: %s)",
				chain, migrationId, m.OldKeyId)
		}
		return false, nil
	})
	if err != nil {
		return 0, err
	}
```
