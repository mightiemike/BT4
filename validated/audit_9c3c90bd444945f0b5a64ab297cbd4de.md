### Title
S4 signed `Envelope` omits namespace/product binding, enabling cross-namespace signature replay - (File: `core/services/s4/envelope.go`)

### Summary
The `Envelope` struct used to authenticate S4 `Put` requests signs only `Address`, `SlotID`, `Payload`, `Version`, and `Expiration` [1](#0-0) . It does not include the storage `namespace` that the S4 ORM uses to segregate different consumers/products of the same shared table [2](#0-1) . This mirrors the `Project.sol` `updateProjectHash` finding: signed data lacks a reference to the "container" (there: project address; here: namespace/product) that the surrounding system otherwise uses to scope the operation, allowing a signature that is valid in one context to be replayed in another.

### Finding Description
`s4.Storage.Put` verifies a submitted signature purely against the `Envelope` (address, slot, payload, version, expiration) recovered via `GetSignerAddress`, and compares the recovered signer to `key.Address` [3](#0-2) . The verification never incorporates `o.namespace`, even though `namespace` is a first-class dimension used by the ORM to keep different S4 "namespaces" (e.g., different products/tables like `SharedTableName`, or per-DON/plugin instances via `NewPostgresORM(ds, tableName, namespace)`) logically separate [4](#0-3) [5](#0-4) .

The same `Envelope`/signature scheme is reused both by the Functions `secrets_set` gateway handler (DON-scoped secrets storage keyed by user address/slot/version) [6](#0-5)  and by the generic OCR2 S4 plugin used for other S4-backed products (via `Row.VerifySignature`, which reconstructs an equivalent `Envelope` from `Address, SlotID, Payload, Version, Expiration` and checks the recovered signer only) [7](#0-6) . Because none of these verification paths bind the signature to a namespace/product/table, if two different S4 namespaces (e.g., two different products, DONs, or the shared table vs. a dedicated Functions secrets table) happen to serve the same user address, slot id, version, and expiration/payload constraints, a signature legitimately produced for one namespace's `Put` would also validate for the other namespace's `Put` — analogous to the original bug where the signed builder/contractor data lacked the project address, letting a signature be replayed once the same nonce recurs on a different project.

### Impact Explanation
If exploitable, a valid signed record submitted by a user for one S4-backed feature/product could be replayed into a different S4-backed namespace sharing the same signature scheme, causing unauthorized data (payload) to be written under that user's identity in a context the user never intended (data/record tampering across product boundaries), without needing the user's private key for that specific product. This is a data-tampering / unauthorized-write class impact bounded to the S4 storage layer's consumers (currently Chainlink Functions secrets and generic S4 OCR2 plugin usage).

### Likelihood Explanation
Exploitability is conditional and not fully confirmed from static inspection alone: it requires (a) two S4 namespaces/tables to be reachable by the same class of caller/attacker, (b) the `(address, slotId, version, payload, expiration)` tuple to coincide or be attacker-influenced across both namespaces, and (c) that no additional out-of-band binding (e.g., the DON/gateway endpoint itself restricting which namespace a given signed request can target) prevents cross-posting. I was not able to fully verify from the indexed code how many distinct S4 namespaces/products exist in production and whether their `Put` entry points are cross-reachable by the same untrusted caller — this would need confirmation via a full repository/deployment review (e.g., `core/services/ocr2/delegate.go` wiring and Functions-specific storage instantiation), which the current index did not surface conclusively.

### Recommendation
Bind the S4 `Envelope` signature to its namespace/product/table context, e.g., include a `Namespace` (or product/table identifier) field in `Envelope` and its canonical JSON (`ToJson`) in `core/services/s4/envelope.go`, and pass/verify that field through `Storage.Put` in `core/services/s4/storage.go` and `Row.VerifySignature` in `core/services/ocr2/plugins/s4/messages.go`, so a signature is only valid for the specific namespace it was created for.

### Proof of Concept
Not independently confirmed against a running deployment; conceptually:
1. Alice signs an `Envelope{Address: alice, SlotID: 0, Payload: P, Version: 1, Expiration: E}` for namespace/product A (e.g., Functions secrets) via `Envelope.Sign` [8](#0-7) , submitted through `handleSecretsSet` → `storage.Put` [6](#0-5) .
2. If an independent S4-backed namespace/product B accepts `Put` requests keyed by the same `(address, slotId, version)` scheme without incorporating namespace into signature verification [3](#0-2) , the identical signature+envelope bytes can be resubmitted to product B's `Put`, and `GetSignerAddress` will recover `alice` and pass verification there as well, writing `P` into namespace B under Alice's slot without her having authorized product B specifically.

### Citations

**File:** core/services/s4/envelope.go (L19-25)
```go
type Envelope struct {
	Address    []byte `json:"address"`
	SlotID     uint   `json:"slotid"`
	Payload    []byte `json:"payload"`
	Version    uint64 `json:"version"`
	Expiration int64  `json:"expiration"`
}
```

**File:** core/services/s4/envelope.go (L38-47)
```go
func (e Envelope) Sign(privateKey *ecdsa.PrivateKey) (signature []byte, err error) {
	if len(e.Address) != common.AddressLength {
		return nil, fmt.Errorf("invalid address length: %d", len(e.Address))
	}
	js, err := e.ToJson()
	if err != nil {
		return nil, err
	}
	return utils.GenerateEthSignature(privateKey, js)
}
```

**File:** core/services/s4/postgres_orm.go (L14-33)
```go
const (
	SharedTableName  = "shared"
	s4PostgresSchema = "s4"
)

type orm struct {
	ds        sqlutil.DataSource
	tableName string
	namespace string
}

var _ ORM = (*orm)(nil)

func NewPostgresORM(ds sqlutil.DataSource, tableName, namespace string) ORM {
	return &orm{
		ds:        ds,
		tableName: fmt.Sprintf(`"%s".%s`, s4PostgresSchema, tableName),
		namespace: namespace,
	}
}
```

**File:** core/services/s4/postgres_orm.go (L35-47)
```go
func (o *orm) Get(ctx context.Context, address *sqlutil.Big, slotId uint) (*Row, error) {
	row := &Row{}

	stmt := fmt.Sprintf(`SELECT address, slot_id, version, expiration, confirmed, payload, signature FROM %s 
WHERE namespace=$1 AND address=$2 AND slot_id=$3;`, o.tableName)
	if err := o.ds.GetContext(ctx, row, stmt, o.namespace, address, slotId); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			err = ErrNotFound
		}
		return nil, err
	}
	return row, nil
}
```

**File:** core/services/s4/storage.go (L148-152)
```go
	envelope := NewEnvelopeFromRecord(key, record)
	signer, err := envelope.GetSignerAddress(signature)
	if err != nil || signer != key.Address {
		return ErrWrongSignature
	}
```

**File:** core/services/functions/connector_handler.go (L212-238)
```go
func (h *functionsConnectorHandler) handleSecretsSet(ctx context.Context, gatewayId string, body *api.MessageBody, fromAddr ethCommon.Address) {
	var request functions.SecretsSetRequest
	var response functions.SecretsSetResponse
	err := json.Unmarshal(body.Payload, &request)
	if err == nil {
		key := s4.Key{
			Address: fromAddr,
			SlotId:  request.SlotID,
			Version: request.Version,
		}
		record := s4.Record{
			Expiration: request.Expiration,
			Payload:    request.Payload,
		}
		h.lggr.Debugw("handling a secrets_set request", "address", fromAddr, "slotId", request.SlotID, "payloadVersion", request.Version, "expiration", request.Expiration)
		err = h.storage.Put(ctx, &key, &record, request.Signature)
		if err == nil {
			response.Success = true
			promStorageUserUpdatesCount.WithLabelValues().Inc()
		} else {
			response.ErrorMessage = fmt.Sprintf("Failed to set secret: %v", err)
		}
	} else {
		response.ErrorMessage = fmt.Sprintf("Bad request to set secret: %v", err)
	}
	h.sendResponseAndLog(ctx, gatewayId, body, response)
}
```

**File:** core/services/ocr2/plugins/s4/messages.go (L65-82)
```go
func (row *Row) VerifySignature() error {
	address := common.BytesToAddress(row.Address)
	e := &s4.Envelope{
		Address:    address.Bytes(),
		SlotID:     uint(row.Slotid),
		Payload:    row.Payload,
		Version:    row.Version,
		Expiration: row.Expiration,
	}
	signer, err := e.GetSignerAddress(row.Signature)
	if err != nil {
		return err
	}
	if !bytes.Equal(signer.Bytes(), address.Bytes()) {
		return s4.ErrWrongSignature
	}
	return nil
}
```
