### Title
Bridge adapter URL registration lacks SSRF protection, allowing internal-network HTTP callbacks from job pipeline execution - ([File: core/web/bridge_types_controller.go])

### Summary
`ValidateBridgeType` in `core/web/bridge_types_controller.go` only checks that the bridge name matches a regex and that the URL string is non-empty; it performs no validation of the URL's host/scheme against link-local, loopback, or other internal-network address ranges. Any authenticated 'edit'-role user can therefore register a bridge whose URL points at internal infrastructure (e.g., `http://169.254.169.254/latest/meta-data/` or `http://localhost:<port>`), and this URL is persisted and later dereferenced by the pipeline bridge task, causing the node to issue outbound HTTP requests to that address on behalf of any job that references the bridge.

### Finding Description
The `POST /v2/bridge_types` endpoint is routed to `BridgeTypesController.Create` (`core/web/bridge_types_controller.go:61`). This handler binds the request JSON into a `bridges.BridgeTypeRequest` (which contains `Name` and `URL models.WebURL`), calls `bridges.NewBridgeType` to produce a `BridgeType`, and then calls `ValidateBridgeType(btr)`: [1](#0-0) 

`ValidateBridgeType` only verifies:
1. the bridge name is non-empty and passes `bridges.ParseBridgeName` (a simple `^[a-zA-Z0-9-_]*$` regex check, `core/bridges/bridge_type.go:146-155`), and
2. the URL string is non-empty (`len(strings.TrimSpace(u)) == 0`).

There is no check of the parsed URL's scheme, host, or IP range — no blocklist/allowlist for loopback (`127.0.0.1`, `localhost`), link-local metadata addresses (`169.254.169.254`), private RFC1918 ranges, or non-http(s) schemes. `bridges.NewBridgeType` (`core/bridges/bridge_type.go:69-96`) also does not perform any such validation; it merely copies `btr.URL` into the persisted `BridgeType`. `models.WebURL` is a light wrapper around `net/url.URL` used purely for (de)serialization, not for host restriction.

Once persisted via `orm.CreateBridgeType`, this URL becomes the target for outbound requests whenever a job pipeline references the bridge task (`core/services/pipeline/task.bridge.go`), which builds an HTTP request to `bt.URL` and sends job-specific parameters as the request body, then returns the response for downstream pipeline processing (e.g., median/aggregation tasks). Because the request is issued directly from the node process/network context, this allows a bridge-owning user to have the node perform internal HTTP requests (e.g., to cloud metadata endpoints or other node-adjacent internal services) and receive the response back through the job's output data path, or to have false data returned into the job's reported answer.

The authorization framework does gate `POST /v2/bridge_types` behind role-based auth (`core/web/router.go` `v2Routes` for `bridge_types`), but the referenced role check only requires 'edit' (a role below full node-operator/admin), and no additional authorization or destination validation is applied at the URL level.

### Impact Explanation
An 'edit'-role user (a role explicitly in-scope per the question's preconditions, and not equivalent to full node-operator/admin trust) can cause the Chainlink node to issue arbitrary outbound HTTP requests to internal/link-local addresses reachable from the node's network context. This can be leveraged for:
- SSRF-style data exfiltration or reconnaissance of internal infrastructure (e.g., cloud instance metadata services exposing IAM credentials on AWS/GCP/Azure via `169.254.169.254`).
- Injecting attacker-controlled or misrouted responses into the job pipeline's bridge task output, potentially corrupting price/report data flowing into downstream OCR/aggregation logic (false price data / misreporting impact).

This matches the "unauthorized external/internal call injection via bridge adapter leading to data exfiltration or false price data" impact class described in the question.

### Likelihood Explanation
The precondition is simply possessing an 'edit' role in the node's authenticated web/API session — no admin privileges, no key leakage, no social engineering required, consistent with the "unprivileged attacker" scope defined by the audit rules (an 'edit' user is a lower-privileged role than full administrator). The exploit is a single authenticated API call (`POST /v2/bridge_types`) followed by creating or having any job reference that bridge task, making it fully reproducible and requiring no race conditions or timing dependencies.

### Recommendation
Add destination validation to `ValidateBridgeType` (or a dedicated URL-safety check invoked from `BridgeTypesController.Create`/`Update`) that:
- Resolves the URL host and rejects loopback, link-local (169.254.0.0/16, fe80::/10), private RFC1918/RFC4193 ranges, and other reserved ranges unless explicitly allowlisted by node configuration.
- Restricts the URL scheme to `http`/`https` only.
- Optionally exposes a node-operator-controlled configuration flag (e.g., `BridgeResponseURL`-style allowlist or `AllowUnrestrictedNetworkAccess`-equivalent flag already used in `task.http.go`) to permit internal bridges only when explicitly enabled by the node operator, mirroring the existing HTTP task restrictions in `core/services/pipeline/task.http.go`.
- Apply the same validation on `Update` (`BridgeTypesController.Update`) since it also calls `ValidateBridgeType` without additional host restriction.

### Proof of Concept
Unit test plan (extend `core/web/bridge_types_controller_test.go` or add a `ValidateBridgeType` fuzz/unit test in `core/bridges`):
1. Construct `bridges.BridgeTypeRequest{Name: "ssrf", URL: models.WebURL{URL: url.Parse("http://169.254.169.254/latest/meta-data/")}}`.
2. Call `ValidateBridgeType(btr)` and assert it currently returns `nil` (no error) — demonstrating the missing check.
3. Repeat with `http://localhost:6688/`, `http://127.0.0.1/`, `file:///etc/passwd` — all currently pass validation.
4. Integration test: start a test node, authenticate as an 'edit'-role user, `POST /v2/bridge_types` with the above SSRF URL, assert `201 Created` is returned (current behavior) instead of the expected `400 Bad Request`.
5. Follow-up integration test: create a job with a bridge task referencing the registered bridge, spin up a mock server bound to `127.0.0.1:<port>`, and confirm the pipeline task issues a request to it and returns the mock response into the job run results — confirming exploitability of the SSRF path end-to-end.

### Citations

**File:** core/web/bridge_types_controller.go (L36-53)
```go
func ValidateBridgeType(bt *bridges.BridgeTypeRequest) error {
	fe := models.NewJSONAPIErrors()
	if len(bt.Name.String()) < 1 {
		fe.Add("No name specified")
	}
	if _, err := bridges.ParseBridgeName(bt.Name.String()); err != nil {
		fe.Merge(err)
	}
	u := bt.URL.String()
	if len(strings.TrimSpace(u)) == 0 {
		fe.Add("URL must be present")
	}
	if bt.MinimumContractPayment != nil &&
		bt.MinimumContractPayment.Cmp(assets.NewLinkFromJuels(0)) < 0 {
		fe.Add("MinimumContractPayment must be positive")
	}
	return fe.CoerceEmptyToNil()
}
```
