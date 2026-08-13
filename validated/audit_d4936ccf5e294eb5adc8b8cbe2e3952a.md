### Title
OCR2 job creation accepts sending/transmitter keys without verifying they are enabled - (File: core/services/job/orm.go)

### Summary
`ValidateKeyStoreMatch`/`validateKeyStoreMatchForRelay` in `core/services/job/orm.go` only verify that a submitted key ID/address exists in the keystore via `.Get()` calls; they never call the keystore's `CheckEnabled` method. This mirrors the reported bug class ("staking key enabled status is not checked in multiple places," allowing a disabled/compromised key to still be accepted).

### Finding Description
When creating or validating an OCR2 job spec, `orm.CreateJob` calls `ValidateKeyStoreMatch`/`areSendingKeysDefined` to confirm that `TransmitterID`/`sendingKeys` correspond to real keystore entries: [1](#0-0) [2](#0-1) 

Each branch of `validateKeyStoreMatchForRelay` simply calls the per-chain keystore's `.Get(...)` method (e.g. `keyStore.Eth().Get(ctx, key)`, `keyStore.Cosmos().Get(key)`, etc.) and only errors if the key is entirely absent from the keystore. None of these branches call `CheckEnabled`, which is the dedicated method that exists specifically to reject disabled keys (as demonstrated for the EVM keystore): [3](#0-2) 

This is directly analogous to the reported issue: the codebase has a well-defined "enabled" concept for keys (`Enable`/`Disable`/`CheckEnabled`, `EnabledKeysForChain`, `EnabledAddresses`), used elsewhere (e.g. `GetRoundRobinAddress` filters to `enabledKeysForChain`, and CLI rebroadcast tooling explicitly checks `CheckEnabled`/disabled state), but the job-creation key-matching path used for OCR2 transmitter/sending-key validation bypasses this check entirely.

I was unable to fully confirm within the available tool budget whether `keyStore.Eth().Get()` internally also checks the enabled state (a `grep_search` for its definition did not return results before iterations ran out), so it remains **possible** that `Get()` incidentally enforces enabled status for the EVM keystore. However, based on `Test_EthKeyStore_CheckEnabled` and the existence of a dedicated `CheckEnabled` API distinct from `Get`, `Get` most plausibly only checks presence in the key ring, not the per-chain "disabled" state stored in `evm.key_states`. This uncertainty should be resolved by inspecting `core/services/keystore/eth.go`'s `Get` implementation directly.

### Impact Explanation
If a sending/transmitter key or OCR key bundle is disabled (e.g., because the corresponding key was known/suspected compromised and an operator disabled it via `Disable`/`/v2/keys/evm/chain?enabled=false`), `ValidateKeyStoreMatch` would still accept it when creating a new OCR2 job spec, since only existence — not enabled state — is checked. This could allow a disabled (compromised) key to be re-registered as an active transmitter/sending key for a job, defeating the purpose of the disable operation and potentially resulting in a compromised key being used to sign/transmit on-chain reports.

### Likelihood Explanation
This requires an actor with node-operator-level access to submit a job spec (already a privileged action), so the practical likelihood is moderate — it applies mainly in incident-response scenarios where an operator disables a compromised key expecting it to be fully excluded from job creation, but the validation logic does not enforce that expectation.

### Recommendation
Update `validateKeyStoreMatchForRelay` (and the LLO/CSA branch in `ValidateKeyStoreMatch`) in `core/services/job/orm.go` to call each keystore's `CheckEnabled` (or equivalent) method in addition to `.Get()`, so that job creation rejects any key that exists but is disabled, consistent with the pattern already established for `eth.CheckEnabled`.

### Proof of Concept
Not directly exploitable by an unprivileged/external actor — requires job-creation privileges. Conceptual reproduction: 1) create and enable an EVM key `K`; 2) disable `K` via `keyStore.Eth().Disable(ctx, K, chainID)`; 3) create an OCR2 job spec with `TransmitterID = K`; `orm.CreateJob` → `ValidateKeyStoreMatch` → `validateKeyStoreMatchForRelay` calls `keyStore.Eth().Get(ctx, K)`, which (per `Test_EthKeyStore_CheckEnabled`'s distinct existence from `Get`) is expected to succeed even though `K` is disabled, allowing the job to be created with a disabled transmitter key.

### Citations

**File:** core/services/job/orm.go (L608-620)
```go
// ValidateKeyStoreMatch confirms that the key has a valid match in the keystore
func ValidateKeyStoreMatch(ctx context.Context, spec *OCR2OracleSpec, keyStore keystore.Master, key string) (err error) {
	switch spec.PluginType {
	case types.LLO:
		_, err = keyStore.CSA().Get(key)
		if err != nil {
			err = errors.Errorf("no CSA key matching: %q", key)
		}
	default:
		err = validateKeyStoreMatchForRelay(ctx, spec.Relay, keyStore, key)
	}
	return
}
```

**File:** core/services/job/orm.go (L622-670)
```go
func validateKeyStoreMatchForRelay(ctx context.Context, network string, keyStore keystore.Master, key string) error {
	switch network {
	case relay.NetworkEVM:
		_, err := keyStore.Eth().Get(ctx, key)
		if err != nil {
			return errors.Errorf("no EVM key matching: %q", key)
		}
	case relay.NetworkCosmos:
		_, err := keyStore.Cosmos().Get(key)
		if err != nil {
			return errors.Errorf("no Cosmos key matching: %q", key)
		}
	case relay.NetworkSolana:
		_, err := keyStore.Solana().Get(key)
		if err != nil {
			return errors.Errorf("no Solana key matching: %q", key)
		}
	case relay.NetworkStarkNet:
		_, err := keyStore.StarkNet().Get(key)
		if err != nil {
			return errors.Errorf("no Starknet key matching: %q", key)
		}
	case relay.NetworkAptos:
		_, err := keyStore.Aptos().Get(key)
		if err != nil {
			return errors.Errorf("no Aptos key matching: %q", key)
		}
	case relay.NetworkTron:
		_, err := keyStore.Tron().Get(key)
		if err != nil {
			return errors.Errorf("no Tron key matching: %q", key)
		}
	case relay.NetworkTON:
		_, err := keyStore.TON().Get(key)
		if err != nil {
			return errors.Errorf("no TON key matching: %q", key)
		}
	case relay.NetworkSui:
		_, err := keyStore.Sui().Get(key)
		if err != nil {
			return errors.Errorf("no Sui key matching: %q", key)
		}
	case relay.NetworkStellar:
		_, err := keyStore.Stellar().Get(key)
		if err != nil {
			return errors.Errorf("no Stellar key matching: %q", key)
		}
	}
	return nil
```

**File:** core/services/keystore/eth.go (L433-475)
```go
// CheckEnabled returns nil if state is present and enabled
// The complexity here comes because we want to return nice, useful error messages
func (ks *eth) CheckEnabled(ctx context.Context, address common.Address, chainID *big.Int) error {
	if utils.IsZero(address) {
		return errors.Errorf("empty address provided as input")
	}
	ks.lock.RLock()
	defer ks.lock.RUnlock()
	if ks.isLocked() {
		return ErrLocked
	}
	var found bool
	for _, k := range ks.keyRing.Eth {
		if k.Address == address {
			found = true
			break
		}
	}
	if !found {
		return errors.Errorf("no eth key exists with address %s", address.String())
	}
	states := ks.keyStates.KeyIDChainID[address.String()]
	state, exists := states[chainID.String()]
	if !exists {
		var chainIDs []string
		for cid, state := range states {
			if !state.Disabled {
				chainIDs = append(chainIDs, cid)
			}
		}
		return errors.Errorf("eth key with address %s exists but is has not been enabled for chain %s (enabled only for chain IDs: %s)", address, chainID.String(), strings.Join(chainIDs, ","))
	}
	if state.Disabled {
		var chainIDs []string
		for cid, state := range states {
			if !state.Disabled {
				chainIDs = append(chainIDs, cid)
			}
		}
		return errors.Errorf("eth key with address %s exists but is disabled for chain %s (enabled only for chain IDs: %s)", address.String(), chainID.String(), strings.Join(chainIDs, ","))
	}
	return nil
}
```
