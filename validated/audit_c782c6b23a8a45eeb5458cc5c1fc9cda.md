### Title
CommitPluginCodecV1 Encode/Decode classify blessed status per-ChainSelector instead of per-root, allowing unblessed merkle roots to be misclassified as blessed on round-trip - (File: core/capabilities/ccip/ccipevm/commitcodec.go)

### Summary
`CommitPluginCodecV1.Encode` and `CommitPluginCodecV1.Decode` both build an `isBlessed` lookup keyed only by `ChainSelector`/`SourceChainSelector` rather than tracking blessed status per individual merkle-root entry. If a single `CommitPluginReport` contains a blessed root and an unblessed root for the *same* source chain selector, both `Encode` and `Decode` will reclassify the unblessed root as blessed (or vice versa depending on ordering), corrupting the blessed/unblessed split that the RMN-blessing security model depends on.

### Finding Description
In `Encode`, `isBlessed` is populated solely from `report.BlessedMerkleRoots[*].ChainSel`: [1](#0-0) 
Then every root — from the concatenation `append(report.BlessedMerkleRoots, report.UnblessedMerkleRoots...)` — is re-bucketed purely by looking up `isBlessed[root.ChainSel]`, not by which original slice it came from: [2](#0-1) 
The same pattern is repeated in `Decode`, where `isBlessed` is derived from `commitReport.BlessedMerkleRoots[*].SourceChainSelector` and then used to re-classify the concatenated list of blessed+unblessed roots: [3](#0-2) 

If a report has two `MerkleRootChain` entries with the same `ChainSel` — one legitimately in `BlessedMerkleRoots` and one in `UnblessedMerkleRoots` — then for every root with that `ChainSel`, `isBlessed[ChainSel]` is `true`, so the unblessed root gets moved into `blessedMerkleRoots` on both `Encode` and `Decode`. This is a per-chain classification bug where the codec should instead preserve the classification per merkle-root entry (which is exactly the information already present via which slice it came from — `BlessedMerkleRoots` vs `UnblessedMerkleRoots`). No signature, replay, or parser check catches this because the corruption happens purely in in-memory Go logic before/after ABI packing; the ABI packing/unpacking itself is lossless, but the surrounding classification logic is not.

The existing unit test `TestCommitPluginCodecV1` in `commitcodec_test.go` never exercises the colliding-`ChainSel` case, so this data-corruption bug is not caught by current tests: [4](#0-3) 
Notably, the sibling `ccipsolana` codec explicitly guards against and rejects mixed blessed/unblessed roots ("both blessed and unblessed merkle roots" test case expects an error), showing the project is aware this scenario is meaningful but the EVM codec has no equivalent guard: [5](#0-4) 

### Impact Explanation
The blessed/unblessed distinction encodes whether a merkle root has been validated by the RMN (Risk Management Network) — blessed roots are attested by RMN signatures and are safe for message execution; unblessed roots are not and require additional/no execution guarantees depending on offramp logic. If this classification collapses per-chain rather than per-root, an unblessed (unvalidated) merkle root can be silently reclassified as blessed after a decode round-trip, which corresponds to bypassing the RMN blessing requirement for that root's messages — enabling execution of messages whose merkle root was never actually attested by RMN. This matches a data-tampering/misreporting impact class affecting CCIP commit report integrity.

### Likelihood Explanation
This requires a `CommitPluginReport` where the same `ChainSelector` appears in both `BlessedMerkleRoots` and `UnblessedMerkleRoots` in one report. This can arise from entirely benign, non-malicious CCIP operation (e.g., RMN blessing lag causing part of a source chain's pending sequence-number ranges to be blessed while a newer range is not yet blessed within the same round) — it does not require attacker control of OCR node internals, only that the upstream plugin logic (in `chainlink-ccip`, outside this repo) produce such a report, which is a plausible and reachable data shape. The bug is deterministic and 100% reproducible with a differential round-trip test.

### Recommendation
Change both `Encode` and `Decode` to preserve blessed/unblessed classification per merkle-root entry rather than per chain selector — e.g., encode/decode the two slices independently without merging into a shared `isBlessed` map keyed by `ChainSel`, or key the map by a composite identity that uniquely distinguishes each root (e.g., `(ChainSel, MinSeqNr, MaxSeqNr, MerkleRoot)`) instead of `ChainSel` alone. Additionally, consider rejecting (or explicitly documenting/handling) reports where the same `ChainSel` appears in both `BlessedMerkleRoots` and `UnblessedMerkleRoots`, similar to the guard already present in `ccipsolana`'s codec.

### Proof of Concept
Add a round-trip test in `core/capabilities/ccip/ccipevm/commitcodec_test.go`:
1. Construct a `cciptypes.CommitPluginReport` with `BlessedMerkleRoots` containing one root with `ChainSel = X` (and a real merkle root `R1`), and `UnblessedMerkleRoots` containing a second, distinct root also with `ChainSel = X` (merkle root `R2`, different seq-num range).
2. Call `commitCodec.Encode(ctx, report)` then `commitCodec.Decode(ctx, encoded)`.
3. Assert that `decodedReport.BlessedMerkleRoots` contains exactly `R1` and `decodedReport.UnblessedMerkleRoots` contains exactly `R2` (matching original classification per root).
4. Expected actual behavior: both `R1` and `R2` end up in `decodedReport.BlessedMerkleRoots` because `isBlessed[X] == true` reclassifies both, demonstrating the round-trip is lossy and violates the per-root invariant.

### Citations

**File:** core/capabilities/ccip/ccipevm/commitcodec.go (L30-34)
```go
func (c *CommitPluginCodecV1) Encode(ctx context.Context, report cciptypes.CommitPluginReport) ([]byte, error) {
	isBlessed := make(map[cciptypes.ChainSelector]bool)
	for _, root := range report.BlessedMerkleRoots {
		isBlessed[root.ChainSel] = true
	}
```

**File:** core/capabilities/ccip/ccipevm/commitcodec.go (L36-52)
```go
	blessedMerkleRoots := make([]ccip_encoding_utils.InternalMerkleRoot, 0, len(report.BlessedMerkleRoots))
	unblessedMerkleRoots := make([]ccip_encoding_utils.InternalMerkleRoot, 0, len(report.UnblessedMerkleRoots))
	for _, root := range append(report.BlessedMerkleRoots, report.UnblessedMerkleRoots...) {
		imr := ccip_encoding_utils.InternalMerkleRoot{
			SourceChainSelector: uint64(root.ChainSel),
			// TODO: abi-encoded address for EVM source, figure out what to do for non-EVM.
			OnRampAddress: common.LeftPadBytes(root.OnRampAddress, 32),
			MinSeqNr:      uint64(root.SeqNumsRange.Start()),
			MaxSeqNr:      uint64(root.SeqNumsRange.End()),
			MerkleRoot:    root.MerkleRoot,
		}
		if isBl, ok := isBlessed[root.ChainSel]; ok && isBl {
			blessedMerkleRoots = append(blessedMerkleRoots, imr)
		} else {
			unblessedMerkleRoots = append(unblessedMerkleRoots, imr)
		}
	}
```

**File:** core/capabilities/ccip/ccipevm/commitcodec.go (L124-146)
```go
	isBlessed := make(map[uint64]bool)
	for _, root := range commitReport.BlessedMerkleRoots {
		isBlessed[root.SourceChainSelector] = true
	}

	blessedMerkleRoots := make([]cciptypes.MerkleRootChain, 0, len(commitReport.BlessedMerkleRoots))
	unblessedMerkleRoots := make([]cciptypes.MerkleRootChain, 0, len(commitReport.UnblessedMerkleRoots))
	for _, root := range append(commitReport.BlessedMerkleRoots, commitReport.UnblessedMerkleRoots...) {
		mrc := cciptypes.MerkleRootChain{
			ChainSel:      cciptypes.ChainSelector(root.SourceChainSelector),
			OnRampAddress: root.OnRampAddress,
			SeqNumsRange: cciptypes.NewSeqNumRange(
				cciptypes.SeqNum(root.MinSeqNr),
				cciptypes.SeqNum(root.MaxSeqNr),
			),
			MerkleRoot: root.MerkleRoot,
		}
		if isBlessed[root.SourceChainSelector] {
			blessedMerkleRoots = append(blessedMerkleRoots, mrc)
		} else {
			unblessedMerkleRoots = append(unblessedMerkleRoots, mrc)
		}
	}
```

**File:** core/capabilities/ccip/ccipevm/commitcodec_test.go (L78-137)
```go
func TestCommitPluginCodecV1(t *testing.T) {
	testCases := []struct {
		name   string
		report func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport
		expErr bool
	}{
		{
			name: "base report",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				return report
			},
		},
		{
			name: "empty token address",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				report.PriceUpdates.TokenPriceUpdates[0].TokenID = ""
				return report
			},
			expErr: true,
		},
		{
			name: "empty merkle root",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				report.BlessedMerkleRoots[0].MerkleRoot = cciptypes.Bytes32{}
				report.UnblessedMerkleRoots[0].MerkleRoot = cciptypes.Bytes32{}
				return report
			},
		},
		{
			name: "zero token price",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				report.PriceUpdates.TokenPriceUpdates[0].Price = cciptypes.NewBigInt(big.NewInt(0))
				return report
			},
		},
		{
			name: "zero gas price",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				report.PriceUpdates.GasPriceUpdates[0].GasPrice = cciptypes.NewBigInt(big.NewInt(0))
				return report
			},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			report := tc.report(randomCommitReport())
			commitCodec := NewCommitPluginCodecV1()
			ctx := t.Context()
			encodedReport, err := commitCodec.Encode(ctx, report)
			if tc.expErr {
				assert.Error(t, err)
				return
			}
			require.NoError(t, err)
			decodedReport, err := commitCodec.Decode(ctx, encodedReport)
			require.NoError(t, err)
			require.Equal(t, report, decodedReport)
		})
	}
```

**File:** core/capabilities/ccip/ccipsolana/commitcodec_test.go (L111-118)
```go
			name: "both blessed and unblessed merkle roots",
			report: func(report cciptypes.CommitPluginReport) cciptypes.CommitPluginReport {
				report.UnblessedMerkleRoots = []cciptypes.MerkleRootChain{
					report.BlessedMerkleRoots[0]}
				return report
			},
			expErr: true,
		},
```
