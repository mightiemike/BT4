# Q1183: GraphQL secret-bearing resolver exposure in DismissJobError

## Question
Can an unprivileged attacker use GraphQL body, variables, aliases, fragments, and auth headers through `POST /query GraphQL mutation `DismissJobError`` so `DismissJobError` or adjacent resolvers return protected key, token, or config material, causing authentication bypass into a privileged mutation and violating GraphQL authorization must be at least as strict as the equivalent REST mutation path?

## Target
- File/function: core/web/resolver/mutation.go::DismissJobError
- Entrypoint: POST /query GraphQL mutation `DismissJobError`
- Attacker controls: GraphQL body, variables, aliases, fragments, and auth headers
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: GraphQL authorization must be at least as strict as the equivalent REST mutation path
- Expected Immunefi impact: authentication bypass into a privileged mutation
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
