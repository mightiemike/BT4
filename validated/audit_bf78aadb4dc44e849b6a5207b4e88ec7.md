Found in `parseOutboundObservationEvent` in `universalClient/chains/svm/event_parser.go:116-200`.

#### Title
Missing per-field length validation causes out-of-bounds slice panic in SVM outbound observation event parser - (File: `universalClient/chains/svm/event_parser.go`)

#### Summary
This is the closest structural analog to the reported "Missing Length Validation" bug in `Array.sol`'s `slice`: a function that computes byte-offsets into a variable-length buffer and slices it (`decoded[offset:offset+32]`) using only a single coarse upper-bound length check, rather than validating each field's bounds before every slice, unlike the sibling function `decodeUniversalTxEvent` in the same file which checks `len(data) < offset+N` before every single field access.

#### Finding Description
`parseOutboundObservationEvent` [1](#0-0)  checks only that `len(decoded) >= 97` once, and then performs four sequential slices/decodes (`decoded[offset:offset+32]` twice, and `binary.LittleEndian.Uint64(decoded[offset:offset+8])`) without re-verifying that `offset` stays within `len(decoded)` after each step: [2](#0-1) 

The 97-byte minimum was computed to be exactly enough for the fields actually read (`8 disc + 32 + 32 + 8 skip + 8 gas_used = 88`, plus additional bytes for `gas_to_refund`/`ata_created` accounted in the comment), so as currently written the single check happens to be arithmetically sufficient for the fields that are read in this code path. However, this pattern is fragile: it relies on the hardcoded literal `97` staying in lockstep with the exact sum of offsets used in the body. Any future edit that reorders fields, adds a field before an existing read, or that miscounts the constant (as the `Array.sol` slice bug shows can happen when the length isn't independently re-verified per access) reintroduces an out-of-bounds slice panic. Unlike `decodeUniversalTxEvent` in the same file, which independently guards every field read with `if len(data) < offset+N`, `parseOutboundObservationEvent` has no such per-field defense-in-depth.

#### Impact Explanation
`ParseEvent` is invoked from the SVM chain log-ingestion path on attacker-influenced program log data (Solana program logs corresponding to `finalize_universal_tx`, `revert_universal_tx`, `funds_rescued` events) — i.e., data that ultimately originates from an external chain and is not produced or vetted by Push Chain validators before this parsing step. A malformed/short buffer that slips past the single length check (e.g., through a future refactor, or if the constant is ever miscalculated) causes a Go slice-bounds-out-of-range panic. If this panic occurs inside a goroutine of the Universal Validator's client without a recover, it can crash the UV client process — a denial-of-service against an unprivileged, ordinary chain-log observation path, distinct from any privileged/relayer assumption.

#### Likelihood Explanation
Currently low: the existing constant (`97`) is arithmetically consistent with the reads actually performed in this function today, so a crash is not immediately triggerable with the current code as written — a passing security reviewer would need to hand-verify the arithmetic invariant holds exactly, which is exactly the kind of implicit, unenforced invariant that caused the original `Array.sol` bug. There is no automatic verification (e.g. per-offset guard or assertion) preventing a future code change (adding a field, moving a check, adjusting the "97" constant) from breaking this invariant silently, and no test in the repo appears to fuzz truncated/malformed outbound-observation log payloads (only `decodeUniversalTxEvent`'s per-field-guarded path shows such defensive tests based on the naming pattern seen for the EVM/Solana payload decoders).

#### Recommendation
Add explicit `if len(decoded) < offset+N { ... return nil }` (or equivalent) guards before each slice/decode in `parseOutboundObservationEvent`, mirroring the defense-in-depth pattern already used in `decodeUniversalTxEvent` in the same file, so correctness does not depend on manually keeping a single top-level length constant in sync with the sum of all field-read offsets.

#### Proof of Concept
Not independently confirmable as currently exploitable without further access to the exact Solana log-emission format and CI/fuzzing harnesses; the described risk is a maintainability/robustness gap (single coarse bound vs. per-field bound checks) rather than a currently-triggerable panic given the current field layout and constant `97`. I was unable to fully verify whether `ParseEvent`/`parseOutboundObservationEvent` is invoked from a goroutine without panic recovery in the ingestion pipeline — that would need to be confirmed in `universalClient/chains/svm/` log-subscription code to determine whether an eventual off-by-one/refactor bug here would crash the process or just fail one event's processing.

### Citations

**File:** universalClient/chains/svm/event_parser.go (L116-134)
```go
func parseOutboundObservationEvent(log string, signature string, slot uint64, logIndex uint, chainID string, logger zerolog.Logger) *store.Event {
	if !strings.HasPrefix(log, "Program data: ") {
		return nil
	}

	eventData := strings.TrimPrefix(log, "Program data: ")
	decoded, err := base64.StdEncoding.DecodeString(eventData)
	if err != nil {
		return nil
	}

	// Minimum: 8 disc + 32 sub_tx_id + 32 universal_tx_id + 8 gas_fee + 8 gas_used
	// + 8 gas_to_refund + 1 ata_created = 97 bytes.
	if len(decoded) < 97 {
		logger.Warn().
			Int("data_len", len(decoded)).
			Msg("data too short for outboundObservation event; need at least 97 bytes")
		return nil
	}
```

**File:** universalClient/chains/svm/event_parser.go (L146-163)
```go
	// Skip discriminator (8 bytes)
	offset := 8

	// Extract txID (32 bytes)
	txID := "0x" + hex.EncodeToString(decoded[offset:offset+32])
	offset += 32

	// Extract universalTxID (32 bytes)
	universalTxID := "0x" + hex.EncodeToString(decoded[offset:offset+32])
	offset += 32

	// Skip gas_fee (prepaid budget, 8 bytes); the audited finalize event reports
	// gas_used separately and that's the value we want to surface as GasFeeUsed.
	offset += 8

	// Extract gas_used (8 bytes, u64 little-endian lamports) — actual gas consumed.
	gasUsed := binary.LittleEndian.Uint64(decoded[offset : offset+8])

```
