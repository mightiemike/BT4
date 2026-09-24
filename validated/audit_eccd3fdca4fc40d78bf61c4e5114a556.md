No vulnerability found for this question.

The reported bug class is a path-traversal-based arbitrary file write in a server that writes files to a shared directory using a user-supplied filename (OpenC3 COSMOS `save_tool_config()`). Searching protobuf-go for an analogous pattern, the only file-writing code paths found are:

- Internal build/codegen tooling (`internal/cmd/generate-types/main.go`, `internal/cmd/generate-protos/main.go`) that writes generated `.go` files based on trusted, developer-controlled paths at build time — not a reachable, unprivileged request path. [1](#0-0) [2](#0-1) 
- The `protoc-gen-go` plugin's `CodeGeneratorResponse_File.Name` field, which is filled in and returned to `protoc` (the caller), not written to disk by protobuf-go itself; the actual filesystem write and the "must not contain `..` components" enforcement is done by `protoc`, an external trusted build tool, not by protobuf-go on an unprivileged network request path. [3](#0-2) 
- Integration test helpers (`integration_test.go`) that download/write files during test execution — not production code. [4](#0-3) 

None of these constitute a reachable, unprivileged production request path where attacker-supplied protobuf message content (parsed via binary or ProtoJSON) determines a filesystem write location, which is the core invariant broken in the OpenC3 report. protobuf-go is a serialization/reflection library and its production decode/encode paths (`proto.Marshal`/`Unmarshal`, `protojson`, `prototext`) do not perform file I/O based on message field values. The closest analog is compiler-plugin tooling that only operates on trusted, locally-supplied `.proto` inputs during code generation, which is explicitly out of scope per the threat model (trusted schema, no privileged/build-time tooling misuse).

### Citations

**File:** internal/cmd/generate-types/main.go (L261-270)
```go
	absFile := filepath.Join(repoRoot, file)
	if run {
		prev, _ := os.ReadFile(absFile)
		if !bytes.Equal(b, prev) {
			fmt.Println("#", file)
			check(os.WriteFile(absFile, b, 0664))
		}
	} else {
		check(os.WriteFile(absFile+".tmp", b, 0664))
		defer os.Remove(absFile + ".tmp")
```

**File:** internal/cmd/generate-protos/main.go (L680-702)
```go
func syncOutput(dstDir, srcDir string) {
	filepath.Walk(srcDir, func(srcPath string, _ os.FileInfo, _ error) error {
		if !strings.HasSuffix(srcPath, ".go") &&
			!strings.HasSuffix(srcPath, ".meta") &&
			!strings.HasSuffix(srcPath, ".proto") {
			return nil
		}
		relPath, err := filepath.Rel(srcDir, srcPath)
		check(err)
		dstPath := filepath.Join(dstDir, relPath)

		if run {
			if copyFile(dstPath, srcPath) {
				fmt.Println("#", relPath)
			}
		} else {
			cmd := exec.Command("diff", dstPath, srcPath, "-N", "-u")
			cmd.Stdout = os.Stdout
			cmd.Run()
		}
		return nil
	})
}
```

**File:** types/pluginpb/plugin.pb.go (L366-379)
```go
type CodeGeneratorResponse_File struct {
	state protoimpl.MessageState `protogen:"open.v1"`
	// The file name, relative to the output directory.  The name must not
	// contain "." or ".." components and must be relative, not be absolute (so,
	// the file cannot lie outside the output directory).  "/" must be used as
	// the path separator, not "\".
	//
	// If the name is omitted, the content will be appended to the previous
	// file.  This allows the generator to break large files into small chunks,
	// and allows the generated text to be streamed back to protoc so that large
	// files need not reside completely in memory at one time.  Note that as of
	// this writing protoc does not optimize for this -- it will read the entire
	// CodeGeneratorResponse before writing files to disk.
	Name *string `protobuf:"bytes,1,opt,name=name" json:"name,omitempty"`
```

**File:** integration_test.go (L358-375)
```go
func downloadFile(check func(error), dstPath, srcURL string, perm fs.FileMode) {
	resp, err := http.Get(srcURL)
	check(err)
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<10))
		check(fmt.Errorf("GET %q: non-200 OK status code: %v body: %q", srcURL, resp.Status, body))
	}

	check(os.MkdirAll(filepath.Dir(dstPath), 0775))
	f, err := os.OpenFile(dstPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, perm)
	check(err)

	_, err = io.Copy(f, resp.Body)
	check(err)

	check(f.Close())
}
```
