### Title
Non-normalized `KeyFor` duplicate check allows case-variant owner IDs to bypass batch dedup while `NormalizeOwner`-based checks treat them as identical - ([File: core/capabilities/vault/validator.go])

### Summary
`RequestValidator.validateWriteRequest` deduplicates batch entries using `vaulttypes.KeyFor(req.Id)`, which concatenates the raw, non-normalized `Owner` field, while `Capability.CreateSecrets`/`UpdateSecrets` separately call `validateEncryptedSecretsUniformOwners`, which compares `vaultutils.NormalizeOwner(enc.Id.Owner)`. Because `ValidateSecretIdentifier`'s regex `^[a-zA-Z0-9_]+$` permits case-variant owner strings like `0xAbC` and `0xabc`, both checks can independently pass for a two-item batch with the same `Key`/`Namespace` but differently-cased `Owner`, defeating the intended duplicate-ID guard.

### Finding Description
`ValidateSecretIdentifier` at [1](#0-0)  only enforces that owner/key/namespace consist of `[a-zA-Z0-9_]+`, so `0xAbC` and `0xabc` are both individually valid identifiers.

`validateWriteRequest`'s duplicate check builds its `uniqueIDs` map from `vaulttypes.KeyFor(req.Id)`: [2](#0-1) , and `KeyFor` concatenates `id.Owner` verbatim without any case normalization: [3](#0-2) .

Meanwhile `validateEncryptedSecretsUniformOwners`, called from `Capability.CreateSecrets`/`UpdateSecrets` before `ValidateCreateSecretsRequest`/`ValidateUpdateSecretsRequest`, compares owners via `vaultutils.NormalizeOwner`, which strips a leading `"0x"` and lowercases: [4](#0-3) [5](#0-4) .

For a batch `[{Owner:"0xAbC", Namespace:"main", Key:"K"}, {Owner:"0xabc", Namespace:"main", Key:"K"}]`:
- `validateEncryptedSecretsUniformOwners` normalizes both owners to `"abc"` and treats them as the *same* owner → check **passes** (no rejection).
- `validateWriteRequest`'s dedup map keys are `"0xAbC::main::K"` and `"0xabc::main::K"`, which are distinct strings → the duplicate check **does not** flag them, so both entries proceed.

The two independent checks use different normalization semantics for the same underlying identity concept (owner), which is the root cause: the invariant "identifier uniqueness must use the same normalized representation used elsewhere for ownership decisions" is violated.

However, whether this actually produces an *exploitable* overwrite/split-brain state downstream depends on how the OCR/vault storage layer (`core/services/ocr2/plugins/vault/kvstore.go`, `plugin.go`) keys secrets for the eventual `Set`/`Get`/`Delete` operations on the DON side — specifically whether that storage layer normalizes the owner (matching `NormalizeOwner`) or uses the raw, case-sensitive owner (matching `KeyFor`). I was not able to confirm within the available index whether the vault plugin's KV store keys secrets using `KeyFor` (raw owner, case-sensitive) or a normalized-owner scheme; this is required to determine definitively whether the two "different" batch entries actually collide into one storage slot (causing a silent overwrite) or are stored as two genuinely separate keys (in which case the only impact is an inconsistent/duplicate-permitting validator, not data corruption).

### Impact Explanation
If the storage layer normalizes owner (as `NormalizeOwner` does), the batch would be accepted as containing "two different" secrets while resolving to a single storage key, causing one entry to silently overwrite the other within the same request — a confusion/state-inconsistency bug that could let an unprivileged caller conceal that a batch actually mutates the same secret twice, potentially bypassing the "duplicate ID" batch-level integrity guarantee. If the storage layer does not normalize (uses raw owner), the vulnerability is limited to bypassing the intended duplicate-detection UX/validation guarantee without deeper data corruption. Given the incomplete visibility into the KV store's key derivation, this is reported as a **confirmed validator-logic inconsistency with a plausible but not fully confirmed storage-level collision impact**.

### Likelihood Explanation
This requires no special privilege — any caller able to submit a `CreateSecretsRequest`/`UpdateSecretsRequest` batch (e.g., via gateway JSON-RPC) with a 2+ item batch containing case-variant owner strings and identical `Key`/`Namespace` can trigger the inconsistency. The regex validation permits arbitrary casing, and no code normalizes `Owner` before it reaches `KeyFor`, making this straightforward and repeatable to reproduce as a validator-layer test.

### Recommendation
Normalize `Owner` consistently everywhere identity/uniqueness decisions are made. Specifically:
- Update `vaulttypes.KeyFor` to use `vaultutils.NormalizeOwner(id.Owner)` instead of the raw `id.Owner` when constructing the dedup/storage key, so it matches the normalization used in `validateEncryptedSecretsUniformOwners`, `validateSecretIdentifiersUniformOwners`, and `Capability.Execute`'s owner-mismatch check.
- Confirm/align the OCR vault plugin's KV store key derivation (`kvstore.go`/`plugin.go`) to use the same normalized owner representation used by `KeyFor`, ensuring uniqueness checks and storage keys are always derived from one canonical function.
- Add a regression test asserting that `validateWriteRequest` rejects a batch with case-variant but otherwise-identical owner/namespace/key as a duplicate.

### Proof of Concept
Add a unit test in `core/capabilities/vault/validator_test.go` (or equivalent):
```go
func TestValidateWriteRequest_CaseVariantOwnerDuplicateBypass(t *testing.T) {
    validator := NewRequestValidator(/* generous limiters */)
    req := &vaultcommon.CreateSecretsRequest{
        RequestId: "req-1",
        EncryptedSecrets: []*vaultcommon.EncryptedSecret{
            {Id: &vaultcommon.SecretIdentifier{Owner: "0xAbC", Namespace: "main", Key: "K"}, EncryptedValue: "<valid-hex-ciphertext>"},
            {Id: &vaultcommon.SecretIdentifier{Owner: "0xabc", Namespace: "main", Key: "K"}, EncryptedValue: "<valid-hex-ciphertext>"},
        },
    }
    err := validator.ValidateCreateSecretsRequest(ctx, nil, req, true)
    // Expected (fixed behavior): err != nil, "duplicate secret ID"
    // Current (buggy behavior): err == nil, both entries pass
    require.Error(t, err)
}
```
Additionally assert directly that `vaulttypes.KeyFor(&vaultcommon.SecretIdentifier{Owner:"0xAbC",...}) != vaulttypes.KeyFor(&vaultcommon.SecretIdentifier{Owner:"0xabc",...})` while `vaultutils.NormalizeOwner("0xAbC") == vaultutils.NormalizeOwner("0xabc")`, demonstrating the mismatch in normalization semantics between the two checks. A follow-up integration test against the OCR vault plugin's KV store would be needed to confirm whether this results in an actual storage-key collision.

### Citations

**File:** core/capabilities/vault/validator.go (L88-93)
```go
		_, ok := uniqueIDs[vaulttypes.KeyFor(req.Id)]
		if ok {
			return errors.New("duplicate secret ID found at index " + strconv.Itoa(idx) + ": " + req.Id.String())
		}

		uniqueIDs[vaulttypes.KeyFor(req.Id)] = true
```

**File:** core/capabilities/vault/validator.go (L124-126)
```go
	if !isValidIDComponent(idKey) || !isValidIDComponent(idOwner) || (idNamespace != "" && !isValidIDComponent(idNamespace)) {
		return errors.New("key, owner and namespace must only contain alphanumeric characters")
	}
```

**File:** core/capabilities/vault/vaulttypes/types.go (L89-92)
```go
func KeyFor(id *vaultcommon.SecretIdentifier) string {
	namespace := NormalizeNamespace(id.Namespace)
	return fmt.Sprintf("%s::%s::%s", id.Owner, namespace, id.Key)
}
```

**File:** core/capabilities/vault/capability.go (L245-260)
```go
func validateEncryptedSecretsUniformOwners(encryptedSecrets []*vaultcommon.EncryptedSecret) error {
	var owner string
	for idx, enc := range encryptedSecrets {
		if enc == nil || enc.Id == nil {
			continue
		}
		if owner == "" {
			owner = enc.Id.Owner
			continue
		}
		if vaultutils.NormalizeOwner(enc.Id.Owner) != vaultutils.NormalizeOwner(owner) {
			return fmt.Errorf("encrypted secret owner at index %d %q does not match batch owner %q", idx, enc.Id.Owner, owner)
		}
	}
	return nil
}
```

**File:** core/capabilities/vault/vaultutils/owner.go (L5-10)
```go
// NormalizeOwner lowercases an Ethereum owner address for case-insensitive comparison.
// All comparison sites must use this function. When VaultOwnerAddressCanonicalizationEnabled
// is introduced, normalization at ingress will supersede comparison-site calls here.
func NormalizeOwner(owner string) string {
	return strings.ToLower(strings.TrimPrefix(owner, "0x"))
}
```
