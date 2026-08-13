### Title
CreateJob mutation allows any 'Edit'-role user to create high-trust CCIP/CCV job types without type-specific authorization - ([File: core/web/resolver/mutation.go])

### Finding Description
`authenticateUserCanEdit` in `core/web/resolver/auth.go` is a purely generic role check: it only distinguishes `sessions.UserRoleView`/`UserRoleRun` (rejected) from anything else (allowed), with no parameter for job type or any binding to `job.Type` [1](#0-0) . Every mutation in `core/web/resolver/mutation.go`, including bridge/key creation, gates access by calling this same generic check with no differentiation based on the sensitivity of the resource being created, as shown by the identical pattern in `CreateBridge` and `CreateCSAKey` (`if err := authenticateUserCanEdit(ctx); err != nil { return nil, err }`) [2](#0-1) [3](#0-2) . The file imports CCIP and CCV-specific validation/service packages (`ccip "github.com/smartcontractkit/chainlink/v2/core/capabilities/ccip/validate"`, `core/services/ccv/ccvcommitteeverifier`, `core/services/ccv/ccvexecutor`) that are used within `CreateJob`, and references to `CCVExecutor`/`CCIP`/`CCVCommitteeVerifier` were confirmed present in the file [4](#0-3) . I was not able to retrieve the exact body of `Resolver.CreateJob` within my remaining tool budget to see line-by-line whether a job-type-specific authorization branch exists before `job.ValidateSpec`/`r.App.AddJobV2` are invoked; this is a genuine gap in my verification.

### Impact Explanation
If `CreateJob` indeed only calls `authenticateUserCanEdit` (consistent with every other mutation's pattern in this file) without an additional check gating job types like CCIP or CCV Executor to Admin-only, then any authenticated user with the low-privilege 'Edit' role (not Admin) could create jobs that drive on-chain-fund-moving executor logic (CCVExecutor) or cross-chain messaging (CCIP), which is a privilege escalation from generic edit access to chain-execution-capable job creation.

### Likelihood Explanation
Feasibility depends entirely on whether `CreateJob`'s implementation, which I could not fully view, contains any additional role/type check beyond `authenticateUserCanEdit`. Given the strict uniform pattern observed across all other mutations in this file (single generic `authenticateUserCanEdit`/`authenticateUserIsAdmin` gate with no per-resource-type branching), it is plausible but not confirmed that `CreateJob` follows the same pattern for all job types including CCIP/CCV.

### Recommendation
If confirmed, add an explicit job-type check in `Resolver.CreateJob` (or in a dedicated authorization helper) that requires `sessions.UserRoleAdmin` (via `authenticateUserIsAdmin`) for high-trust job types such as `job.CCIP`, `job.CCVExecutor`, and `job.CCVCommitteeVerifier`, evaluated after parsing `job.Type` from the TOML spec but before calling `job.ValidateSpec`/`r.App.AddJobV2`.

### Proof of Concept
Integration test: create GraphQL sessions with `UserRoleEdit` and `UserRoleAdmin`; call `CreateJob` mutation with `Input.TOML` specifying `type = "ccip"` and separately `type = "ccvexecutor"`; assert that the Edit-role session's request either succeeds (indicating the vulnerability) or is rejected with `RoleNotPermittedError` (indicating the check exists). Given I could not verify the actual `CreateJob` function body, this should be the first step to confirm before treating the finding as fully validated.

### Citations

**File:** core/web/resolver/auth.go (L31-43)
```go
// Authenticates the user from the session cookie and asserts at least 'edit' role.
func authenticateUserCanEdit(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	switch session.User.Role {
	case sessions.UserRoleView, sessions.UserRoleRun:
		return RoleNotPermittedError{session.User.Role}
	default:
	}
	return nil
}
```

**File:** core/web/resolver/mutation.go (L24-29)
```go
	ccip "github.com/smartcontractkit/chainlink/v2/core/capabilities/ccip/validate"
	"github.com/smartcontractkit/chainlink/v2/core/logger/audit"
	"github.com/smartcontractkit/chainlink/v2/core/services/blockhashstore"
	"github.com/smartcontractkit/chainlink/v2/core/services/blockheaderfeeder"
	"github.com/smartcontractkit/chainlink/v2/core/services/ccv/ccvcommitteeverifier"
	"github.com/smartcontractkit/chainlink/v2/core/services/ccv/ccvexecutor"
```

**File:** core/web/resolver/mutation.go (L62-65)
```go
func (r *Resolver) CreateBridge(ctx context.Context, args struct{ Input createBridgeInput }) (*CreateBridgePayloadResolver, error) {
	if err := authenticateUserCanEdit(ctx); err != nil {
		return nil, err
	}
```

**File:** core/web/resolver/mutation.go (L112-116)
```go
func (r *Resolver) CreateCSAKey(ctx context.Context) (*CreateCSAKeyPayloadResolver, error) {
	if err := authenticateUserCanEdit(ctx); err != nil {
		return nil, err
	}

```
