# Q1166: Boundary preservation edge case in DeleteVRFKey #3

## Question
Can an unprivileged attacker use shared variables reused across multiple aliased mutations at `POST /query GraphQL mutation `DeleteVRFKey`` so `DeleteVRFKey` reaches a concrete path to authentication bypass into a privileged mutation by breaking the invariant that GraphQL authorization must be at least as strict as the equivalent REST mutation path, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/resolver/mutation.go::DeleteVRFKey
- Entrypoint: POST /query GraphQL mutation `DeleteVRFKey`
- Attacker controls: shared variables reused across multiple aliased mutations
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: GraphQL authorization must be at least as strict as the equivalent REST mutation path
- Expected Immunefi impact: authentication bypass into a privileged mutation
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
