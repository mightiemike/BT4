### Title
Timeout/RPC failure on destination chain during reader/writer creation is silently treated as "unsupported chain family," allowing a stalled/degraded RPC to force a silent fallback to `NewNoOpTransmitter` for CCIP transmission - ([File: core/capabilities/ccip/oraclecreator/plugin.go])

### Summary
`createReadersAndWriters` bounds each relayer's `GetChainReader`/`GetChainWriter` call with a 10s `readerWriterCreationTimeout`, but any error from these calls — including a context-deadline-exceeded from an unresponsive/degraded RPC — is swallowed at `Debugf` level and treated identically to "this chain family doesn't need a reader/writer." For chain families whose `CCIPProvider` supplies a `ChainAccessor` but returns a nil `ContractTransmitter`, this causes `destChainWriter` to be absent from `chainWriters`, and `createFactoryAndTransmitter` silently falls into the "case 3" branch, constructing a `NewNoOpTransmitter` instead of returning an error. `Create` then returns a healthy-looking oracle that never actually transmits.

### Finding Description
In `createReadersAndWriters` [1](#0-0) , each relayer's `GetChainReader`/`GetChainWriter` call is executed under a per-chain `chainCtx` bounded to `readerWriterCreationTimeout` (10s) [2](#0-1) . When `crcw.GetChainReader`/`GetChainWriter` returns any error — whether because the chain family genuinely has no provider registered (`MultiChainRW.GetChainReader/GetChainWriter` returning `"unsupported chain family %s"` [3](#0-2) ) or because the underlying `params.Relayer.NewContractReader/NewContractWriter` call failed due to a slow/unresponsive RPC and hit the context deadline (e.g. in the EVM/Aptos/Sui providers which just forward `ctx` to `params.Relayer.NewContractReader`/`NewContractWriter` [4](#0-3) ) — the code takes the exact same branch: it logs at `Debugf` and returns `nil` from the per-chain closure, silently continuing the loop [5](#0-4) [6](#0-5) . There is no way to distinguish "not applicable for this chain family" from "actual RPC failure/timeout," and this applies even when the affected chain is the DON's destination chain.

Back in `Create`, `destChainWriter, ok := chainWriters[config.Config.ChainSelector]` will be missing, and only an `Infow` log is emitted, not an error [7](#0-6) . For chain families where `CCIPProvider` supplies a `ChainAccessor` but its `ContractTransmitter()` is nil, `getChainAccessorsAndContractTransmittersFromProviders` does not perform the missing-reader/writer sanity check (that check is only exercised in the `else` branch used when no `CCIPProvider` exists for the chain selector) [8](#0-7) . So no explicit error is raised at that layer either. Then in `createFactoryAndTransmitter`, the transmitter selection switch falls through case 1 (`ct` is nil) and case 2 (`destChainWriter` is nil) straight into the default "case 3," which logs at `Infow` and silently constructs `ocrimpls.NewNoOpTransmitter` [9](#0-8) . `Create` then returns success with a fully constructed, apparently healthy `wrappedOracle`.

### Impact Explanation
A degraded/unresponsive RPC endpoint for the destination chain during DON (re)creation results in the oracle being created successfully but with a `NoOpTransmitter` instead of a real on-chain transmitter for the affected chain-family class. This causes silent, unauthorized non-execution of CCIP report transmission while all node-level signals (log level `Info`, no returned error, healthy oracle object) suggest normal operation — matching a misreporting/availability-masking bounty class (transmission is disabled without operator-visible failure).

### Likelihood Explanation
Exploitability depends entirely on an external condition — a slow, unresponsive, or intermittently failing RPC endpoint for the destination chain that is exercised during DON (re)launch, which can plausibly be triggered by ordinary network degradation or a third-party RPC provider issue, not requiring node-operator privileges. It is limited to chain families whose `CCIPProvider` supplies a `ChainAccessor` without a `ContractTransmitter` (case 1 not satisfied, `ct == nil`), and requires the timing window of a DON creation/relaunch coinciding with the RPC failure, making it opportunistic but repeatable given a persistently degraded endpoint.

### Recommendation
Distinguish "chain family provider not registered" (a static, expected condition) from "provider call failed" (e.g. context deadline exceeded or other error) in `createReadersAndWriters`; only silently skip in the former case, and propagate/return an explicit error in the latter, especially when the affected chain equals the destination chain. Additionally, in `createFactoryAndTransmitter`, when `destChainWriter == nil` and case 1 is not satisfied for the DON's destination chain, return an explicit error instead of silently falling back to `NewNoOpTransmitter`.

### Proof of Concept
Add a unit test around `pluginOracleCreator.createReadersAndWriters`/`Create` with a fake `ChainRWProvider` (or fake relayer) for the destination chain family whose `GetChainWriter` blocks past `readerWriterCreationTimeout` (or returns `context.DeadlineExceeded`) while `GetChainReader` succeeds, and a fake `CCIPProvider` for that relay ID that returns a non-nil `ChainAccessor` but a nil `ContractTransmitter`. Assert that:
1. `createReadersAndWriters` (or `Create`) returns a non-nil error rather than silently omitting the destination chain writer.
2. If (1) currently passes without error, assert instead (documenting the bug) that `Create` succeeds and the returned `ContractTransmitter` is a `*ocrimpls.NoOpTransmitter` for the destination chain — proving the silent fallback occurs without any propagated error.

### Citations

**File:** core/capabilities/ccip/oraclecreator/plugin.go (L235-240)
```go
	destChainWriter, ok := chainWriters[config.Config.ChainSelector]
	if !ok {
		i.lggr.Infow("no chain writer found for dest chain, will create nil transmitter",
			"destChainID", destChainID,
			"destChainSelector", config.Config.ChainSelector)
	}
```

**File:** core/capabilities/ccip/oraclecreator/plugin.go (L405-423)
```go
		default:
			// case 3
			i.lggr.Infow("no chain writer found for dest chain, creating nil transmitter",
				"destChainID", destChainID,
				"destChainSelector", config.Config.ChainSelector)
			transmitAccount, err := i.getTransmitterFromPublicConfig(publicConfig)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to get transmitter from public config: %w", err)
			}
			i.lggr.Infow("using (fake) transmitter from public config in the commit no-op transmitter", "transmitAccount", transmitAccount)
			transmitter = ocrimpls.NewNoOpTransmitter(
				i.lggr.
					Named("CCIPCommitNoOpTransmitter").
					Named(destRelayID.String()).
					Named(fmt.Sprintf("%d", config.Config.ChainSelector)),
				i.p2pID.PeerID().String(),
				transmitAccount,
			)
		}
```

**File:** core/capabilities/ccip/oraclecreator/plugin.go (L565-593)
```go
		ccipProvider := ccipProviders[chainSelector]
		if ccipProvider != nil {
			ca = ccipProvider.ChainAccessor()
			if ca == nil {
				return nil, nil, fmt.Errorf("CCIPProvider for relay ID %s does not support chain accessor", relayID)
			}
			ct = ccipProvider.ContractTransmitter()
			if ct == nil {
				i.lggr.Warnw("contracts transmitter provided from CCIP provider is nil, will use default transmitter if possible",
					"relayID", relayID,
					"chainSelector", chainSelector,
				)
			}
		} else {
			// Use DefaultAccessor if CR and CW exist
			if extendedReaders[chainSelector] == nil || chainWriters[chainSelector] == nil {
				return nil, nil, fmt.Errorf("cannot create default chain accessor for relay ID %s, contract reader and chain writer need to be present", relayID)
			}
			ca, err = chainaccessor.NewDefaultAccessor(
				i.lggr,
				chainSelector,
				extendedReaders[chainSelector],
				chainWriters[chainSelector],
				pluginServices.AddrCodec,
			)
			if err != nil {
				return nil, nil, fmt.Errorf("failed to create default chain accessor for relay ID %s: %w", relayID, err)
			}
		}
```

**File:** core/capabilities/ccip/oraclecreator/plugin.go (L691-723)
```go
	for relayID, relayer := range i.relayers {
		if err := func() error {
			// Bound each chain's reader/writer creation with its own deadline so an unavailable
			// chain LOOPP fails fast instead of blocking the launcher context
			// forever.
			chainCtx, cancel := context.WithTimeout(ctx, readerWriterCreationTimeout)
			defer cancel()

			chainID := relayID.ChainID
			relayChainFamily := relayID.Network
			chainDetails, err1 := chainsel.GetChainDetailsByChainIDAndFamily(chainID, relayChainFamily)
			chainSelector := cciptypes.ChainSelector(chainDetails.ChainSelector)
			if err1 != nil {
				return fmt.Errorf("failed to get chain selector from chain ID %s: %w", chainID, err1)
			}

			cr, err1 := crcw.GetChainReader(chainCtx, ccipcommon.ChainReaderProviderOpts{
				Lggr:            i.lggr,
				Relayer:         relayer,
				ChainID:         chainID,
				DestChainID:     destChainID,
				HomeChainID:     homeChainID,
				Ofc:             ofc,
				ChainSelector:   chainSelector,
				ChainFamily:     relayChainFamily,
				DestChainFamily: destChainFamily,
				Transmitters:    i.transmitters,
			})
			if err1 != nil {
				// Some Chain family might not need crcw to be created, and if createChainAccessorsAndContractTransmitters will catch error if it does
				i.lggr.Debugf("skipping creating reader and writers for chain %s, reader creation: %v", chainID, err1)
				return nil
			}
```

**File:** core/capabilities/ccip/oraclecreator/plugin.go (L751-755)
```go
			if err1 != nil {
				// Some Chain family might not need crcw to be created, and if createChainAccessorsAndContractTransmitters will catch error if it does
				i.lggr.Debugf("skipping creating chain writer for chain %s, writer creation: %v", chainID, err1)
				return nil
			}
```

**File:** core/capabilities/ccip/common/crcwconfig.go (L56-74)
```go
// GetChainReader returns a new ContractReader base on relay chain family.
func (c *MultiChainRW) GetChainReader(ctx context.Context, params ChainReaderProviderOpts) (types.ContractReader, error) {
	provider, exist := c.cwProviderMap[params.ChainFamily]
	if !exist {
		return nil, fmt.Errorf("unsupported chain family %s", params.ChainFamily)
	}

	return provider.GetChainReader(ctx, params)
}

// GetChainWriter returns a new ContractWriter based on relay chain family.
func (c *MultiChainRW) GetChainWriter(ctx context.Context, params ChainWriterProviderOpts) (types.ContractWriter, error) {
	provider, exist := c.cwProviderMap[params.ChainFamily]
	if !exist {
		return nil, fmt.Errorf("unsupported chain family %s", params.ChainFamily)
	}

	return provider.GetChainWriter(ctx, params)
}
```

**File:** core/capabilities/ccip/ccipevm/crcwconfig.go (L48-53)
```go
	cr, err := params.Relayer.NewContractReader(ctx, marshaledConfig)
	if err != nil {
		return nil, err
	}

	return cr, nil
```
