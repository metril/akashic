package metadata

import (
	"strings"
	"unicode/utf8"

	"github.com/pkg/xattr"
)

// CollectXattrs returns the user-visible extended attributes on `path` keyed
// by name. Binary values are base64-encoded with a "base64:" prefix so the
// JSONB column always carries valid UTF-8.
//
// We deliberately skip kernel-internal namespaces (system.*, security.*) when
// they aren't readable by an unprivileged scanner; xattr.LList only returns
// what the caller is allowed to see.
func CollectXattrs(path string) (map[string]string, error) {
	names, err := xattr.LList(path)
	if err != nil {
		// ENOTSUP / EOPNOTSUPP / ENOATTR → no xattrs, not an error.
		return nil, nil
	}
	if len(names) == 0 {
		return nil, nil
	}
	out := make(map[string]string, len(names))
	for _, name := range names {
		val, err := xattr.LGet(path, name)
		if err != nil {
			continue
		}
		if utf8.Valid(val) && !containsBinary(val) {
			out[name] = string(val)
		} else {
			out[name] = "base64:" + base64Encode(val)
		}
	}
	if len(out) == 0 {
		return nil, nil
	}
	return out, nil
}

func containsBinary(b []byte) bool {
	for _, c := range b {
		if c == 0 {
			return true
		}
	}
	return false
}

// Lazy import: stdlib base64 is small enough to inline-import locally.
func base64Encode(b []byte) string {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var sb strings.Builder
	sb.Grow(((len(b) + 2) / 3) * 4)
	for i := 0; i < len(b); i += 3 {
		var n uint32
		switch len(b) - i {
		case 1:
			n = uint32(b[i]) << 16
			sb.WriteByte(alphabet[(n>>18)&0x3f])
			sb.WriteByte(alphabet[(n>>12)&0x3f])
			sb.WriteString("==")
		case 2:
			n = uint32(b[i])<<16 | uint32(b[i+1])<<8
			sb.WriteByte(alphabet[(n>>18)&0x3f])
			sb.WriteByte(alphabet[(n>>12)&0x3f])
			sb.WriteByte(alphabet[(n>>6)&0x3f])
			sb.WriteByte('=')
		default:
			n = uint32(b[i])<<16 | uint32(b[i+1])<<8 | uint32(b[i+2])
			sb.WriteByte(alphabet[(n>>18)&0x3f])
			sb.WriteByte(alphabet[(n>>12)&0x3f])
			sb.WriteByte(alphabet[(n>>6)&0x3f])
			sb.WriteByte(alphabet[n&0x3f])
		}
	}
	return sb.String()
}
