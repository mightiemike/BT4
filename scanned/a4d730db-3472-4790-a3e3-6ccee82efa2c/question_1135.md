# Q1135: Alias or fragment privilege mixup in DeleteJob

## Question
Can an unprivileged attacker exploit shared variables reused across multiple aliased mutations through `POST /query GraphQL mutation `DeleteJob`` so `DeleteJob` smuggles a privileged side effect through aliasing, fragment reuse, or nested inputs, leading to execute arbitrary system commands if a protected mutation reaches a dangerous backend path and breaking one GraphQL operation must not smuggle privileged side effects through aliasing or fragment reuse?

## Target
- File/function: core/web/resolver/mutation.go::DeleteJob
- Entrypoint: POST /query GraphQL mutation `DeleteJob`
- Attacker controls: shared variables reused across multiple aliased mutations
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: one GraphQL operation must not smuggle privileged side effects through aliasing or fragment reuse
- Expected Immunefi impact: execute arbitrary system commands if a protected mutation reaches a dangerous backend path
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
