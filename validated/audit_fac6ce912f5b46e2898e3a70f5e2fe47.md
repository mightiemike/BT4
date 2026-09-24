No vulnerability found for this question.

The reported bug class is a Zip Slip / path traversal during archive extraction, where attacker-controlled file paths inside a zip escape the intended destination directory. Searching protobuf-go for any analogous "write to a path derived from untrusted input" pattern found no such reachable behavior:

- The only file-path-driven write logic is in `CodeGeneratorResponse_File` used by the `protoc`/plugin code-generation protocol [1](#0-0) , which is a local, trusted build-time tool invocation, not an unprivileged network request path, and protoc itself (not protobuf-go) is responsible for writing files.
- `Any` message handling (`FindMessageByURL`, `UnmarshalNew`, `marshalAny`/`unmarshalAny`) resolves type names into registered Go message types via `protoregistry.Types.FindMessageByURL` [2](#0-1)  — this only looks up pre-registered types by name/suffix; it never uses attacker input to construct a filesystem path or write outside an intended directory.
- `internal/cmd/generate-protos/main.go`'s `downloadArchive`/`copyFile` helpers do path joins based on tar entry names [3](#0-2) , but these are internal developer/test tooling run locally against trusted release archives, not a reachable production request path.

No code path in protobuf-go extracts an archive or writes files to disk based on attacker-supplied names reachable via an unprivileged request, so there is no analog to the zip-local Zip Slip vulnerability.

### Citations

**File:** types/pluginpb/plugin.pb.go (L366-380)
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
	// If non-empty, indicates that the named file should already exist, and the
```

**File:** reflect/protoregistry/registry.go (L636-657)
```go
func (r *Types) FindMessageByURL(url string) (protoreflect.MessageType, error) {
	// This function is similar to FindMessageByName but
	// truncates anything before and including '/' in the URL.
	if r == nil {
		return nil, NotFound
	}
	if r == GlobalTypes {
		globalMutex.RLock()
		defer globalMutex.RUnlock()
	}
	message := protoreflect.FullName(url)
	if i := strings.LastIndexByte(url, '/'); i >= 0 {
		message = message[i+len("/"):]
	}

	if v := r.typesByName[message]; v != nil {
		if mt, _ := v.(protoreflect.MessageType); mt != nil {
			return mt, nil
		}
		return nil, errors.New("found wrong type: got %v, want message", typeName(v))
	}
	return nil, NotFound
```

**File:** integration_test.go (L410-431)
```go
		// Skip directories or files outside the prefix directory.
		if len(skipPrefix) > 0 {
			if !strings.HasPrefix(h.Name, skipPrefix) {
				continue
			}
			if len(h.Name) > len(skipPrefix) && h.Name[len(skipPrefix)] != '/' {
				continue
			}
		}

		path := strings.TrimPrefix(strings.TrimPrefix(h.Name, skipPrefix), "/")
		path = filepath.Join(dstPath, filepath.FromSlash(path))
		mode := os.FileMode(h.Mode & 0777)
		switch h.Typeflag {
		case tar.TypeReg:
			b, err := io.ReadAll(tr)
			check(err)
			check(os.WriteFile(path, b, mode))
		case tar.TypeDir:
			check(os.Mkdir(path, mode))
		}
	}
```
