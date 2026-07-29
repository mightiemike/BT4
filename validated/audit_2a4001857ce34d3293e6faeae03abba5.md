### Title
Dead-code fallback in EVM `getRequiredConfirmations` allows zero-confirmation finality gating - (File: `universalClient/chains/evm/event_confirmer.go`)

### Summary
`EventConfirmer.getRequiredConfirmations` in the EVM universal-client uses `>= 0` guards on `uint64` fields (`fastConfirmations`, `standardConfirmations`) to decide whether to use the configured value or fall back to a safe default (5 / 12). Because these fields are unsigned, `>= 0` is always true, so the "fallback to default" branch is unreachable dead code. The SVM counterpart (`universalClient/chains/svm/event_confirmer.go`) uses the correct `> 0` check. This is the same bug class as UF-5 (wrong comparison operator at a boundary causing the wrong branch to be taken), except here the miscomparison silently accepts `0` as a legitimate "required confirmations" value instead of falling back to the intended safe minimum.

### Finding Description
`getRequiredConfirmations` (`universalClient/chains/evm/event_confirmer.go:248-268`):
```go
func (ec *EventConfirmer) getRequiredConfirmations(confirmationType string) uint64 {
	switch confirmationType {
	case store.ConfirmationFast:
		if ec.fastConfirmations >= 0 {
			return ec.fastConfirmations
		}
		return 5
	case store.ConfirmationStandard:
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	default:
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	}
}
``` [1](#0-0) 

`fastConfirmations`/`standardConfirmations` are `uint64` (populated from `applyDefaults()` / registry `BlockConfirmation`), so `>= 0` is a tautology — the `return 5` / `return 12` fallback lines can never execute. Contrast this with the SVM implementation, which correctly guards with `> 0`: [2](#0-1) 

The confirmation requirement feeds directly into the inbound-finality gate:
```go
confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1
if confirmations >= requiredConfirmations {
    ... mark event CONFIRMED ...
}
``` [3](#0-2) 

A CONFIRMED event is what feeds the `VoteInbound` path that mints PRC20 / deposits funds via the universal-execution flow, so `requiredConfirmations` is a safety boundary that governs source-chain reorg protection. [4](#0-3) 

If `fastConfirmations` or `standardConfirmations` ends up as `0` — e.g., a registry `BlockConfirmation` config that only sets one of `FastInbound`/`StandardInbound` (proto3 zero-value default), or any config path that leaves the field unset — `applyDefaults()` copies that `0` straight through:
```go
if c.registryConfig != nil && c.registryConfig.BlockConfirmation != nil {
    config.fastConfirmations = uint64(c.registryConfig.BlockConfirmation.FastInbound)
    config.standardConfirmations = uint64(c.registryConfig.BlockConfirmation.StandardInbound)
}
``` [5](#0-4) 

With the broken `>= 0` guard, `getRequiredConfirmations` then returns `0` (instead of the intended safe default of 5/12), and any event with at least 1 confirmation (`confirmations >= 0` is always satisfied) is immediately marked `CONFIRMED` and proceeds to `VoteInbound`.

### Impact Explanation
This weakens (effectively removes) the block-confirmation finality gate that is supposed to protect against source-chain reorgs. An unprivileged external attacker who can produce a source-chain deposit/gateway event that later gets reorged out could have it voted as an inbound and executed on Push Chain (minting PRC20 / crediting a UEA) before the source-chain transaction is actually final, because the client treats a `0`-confirmation requirement as valid rather than falling back to the safe default. This maps to the "corruption of PRC20 accounting" / "forged inbound accepted through user-reachable flows with honest validators" impact categories, since honest validators running this exact code would vote based on a prematurely-confirmed (and potentially reorg-vulnerable) event.

### Likelihood Explanation
Reachability depends on `BlockConfirmation.FastInbound`/`StandardInbound` being `0` for a given chain config — which can occur from an admin partially specifying `BlockConfirmation` (e.g. setting only `StandardInbound` and leaving `FastInbound` unset, which defaults to `0` in proto3), or any registry entry where the value is not explicitly populated. This is a config-dependent condition rather than a pure attacker-only trigger, so likelihood is moderate: it requires a specific (but plausible and easy-to-hit) registry configuration state rather than privileged/malicious action, and the bug is a straightforward logic defect (dead branch) present in shipped code today.

### Recommendation
Change the guards in `universalClient/chains/evm/event_confirmer.go:getRequiredConfirmations` from `>= 0` to `> 0` for both `ec.fastConfirmations` and `ec.standardConfirmations`, matching the SVM implementation, so a `0`/unset value correctly falls back to the safe defaults (5 fast / 12 standard) instead of being treated as "zero confirmations required."

### Proof of Concept
1. Configure (or leave default/unset) a chain's `uregistry` `ChainConfig.BlockConfirmation.FastInbound` (or `StandardInbound`) to `0`.
2. The EVM `Client.applyDefaults()` copies this `0` into `componentConfig.fastConfirmations` (or `standardConfirmations`). [6](#0-5) 
3. `EventConfirmer.getRequiredConfirmations(store.ConfirmationFast)` evaluates `ec.fastConfirmations >= 0` → always `true` → returns `0` instead of the intended default `5`. [7](#0-6) 
4. In `processPendingEvents`, any pending event with `confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1 >= 0` (always true) is immediately marked `CONFIRMED`, one block after inclusion, regardless of reorg risk. [3](#0-2) 
5. The event proceeds to `VoteInbound`, and once 2/3+ validators (all running the same buggy client) vote, Push Chain executes the inbound (mint/deposit) before the source-chain transaction is actually finalized, exposing the mint to a subsequent reorg on the source chain.

**Note on verification limits:** I was unable to directly confirm via the index whether `fastConfirmations`/`standardConfirmations` in the `EventConfirmer` struct are declared as `uint64` (vs. a signed type) — this is inferred from the `applyDefaults()` `uint64(...)` casts, the constructor signatures seen in tests (e.g. `NewEventConfirmer(nil, nil, "eip155:1", 5, 5, 12, logger)`), and the parallel SVM struct/tests using the same types with a correct `> 0` check. A Devin session with full repo access should confirm the exact field type declaration before landing the fix, though the behavioral evidence (dead `return 5`/`return 12` branches, confirmed by test cases exercising `0` inputs) strongly indicates the described bug.

### Citations

**File:** universalClient/chains/evm/event_confirmer.go (L159-165)
```go
		}

		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1

		if confirmations >= requiredConfirmations {
```

**File:** universalClient/chains/evm/event_confirmer.go (L248-268)
```go
// getRequiredConfirmations returns the required number of confirmations based on confirmation type
func (ec *EventConfirmer) getRequiredConfirmations(confirmationType string) uint64 {
	switch confirmationType {
	case store.ConfirmationFast:
		if ec.fastConfirmations >= 0 {
			return ec.fastConfirmations
		}
		return 5
	case store.ConfirmationStandard:
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	default:
		// Default to standard if unknown
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	}
}
```

**File:** universalClient/chains/svm/event_confirmer.go (L228-248)
```go
// getRequiredConfirmations returns the required number of confirmations based on confirmation type
func (ec *EventConfirmer) getRequiredConfirmations(confirmationType string) uint64 {
	switch confirmationType {
	case store.ConfirmationFast:
		if ec.fastConfirmations > 0 {
			return ec.fastConfirmations
		}
		return 5
	case store.ConfirmationStandard:
		if ec.standardConfirmations > 0 {
			return ec.standardConfirmations
		}
		return 12
	default:
		// Default to standard if unknown
		if ec.standardConfirmations > 0 {
			return ec.standardConfirmations
		}
		return 12
	}
}
```

**File:** universalClient/README.md (L80-84)
```markdown
1. The EVM/SVM event listener detects a gateway event on the source chain
2. The event confirmer waits for sufficient block confirmations to ensure finality
3. The event processor votes the observation onto Push Chain via `VoteInbound`
4. Once 2/3+ validators agree, Push Chain executes the inbound (deposits funds, runs payload)
5. If the execution produces outbound events, those become pending outbounds
```

**File:** universalClient/chains/evm/client.go (L354-382)
```go
// applyDefaults applies default values to all component configuration
func (c *Client) applyDefaults() componentConfig {
	config := componentConfig{
		eventPollingInterval:  5,  // default
		gasPriceInterval:      30, // default
		fastConfirmations:     2,
		standardConfirmations: 12,
	}

	// Apply event polling interval
	if c.chainConfig != nil && c.chainConfig.EventPollingIntervalSeconds != nil && *c.chainConfig.EventPollingIntervalSeconds > 0 {
		config.eventPollingInterval = *c.chainConfig.EventPollingIntervalSeconds
	}

	// Apply gas price interval
	if c.chainConfig != nil && c.chainConfig.GasPriceIntervalSeconds != nil && *c.chainConfig.GasPriceIntervalSeconds > 0 {
		config.gasPriceInterval = *c.chainConfig.GasPriceIntervalSeconds
	}

	// Apply gas price markup percent
	if c.chainConfig != nil && c.chainConfig.GasPriceMarkupPercent != nil && *c.chainConfig.GasPriceMarkupPercent > 0 {
		config.gasPriceMarkupPercent = *c.chainConfig.GasPriceMarkupPercent
	}

	// Apply confirmation requirements
	if c.registryConfig != nil && c.registryConfig.BlockConfirmation != nil {
		config.fastConfirmations = uint64(c.registryConfig.BlockConfirmation.FastInbound)
		config.standardConfirmations = uint64(c.registryConfig.BlockConfirmation.StandardInbound)
	}
```
