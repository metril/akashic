package metadata

import (
	"strings"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// ParseRemotePosixDump consumes concatenated `getfacl` output (one stanza per
// file, separated by blank lines, each starting with `# file: <path>`).
// Returns a map keyed by absolute path.
func ParseRemotePosixDump(raw string) map[string]*models.ACL {
	out := make(map[string]*models.ACL)
	for _, stanza := range splitStanzas(raw) {
		path, body := extractPathAndBody(stanza)
		if path == "" {
			continue
		}
		access, defaults := parsePosixACL(body)
		if access == nil && defaults == nil {
			continue
		}
		out[path] = &models.ACL{
			Type:           "posix",
			Entries:        access,
			DefaultEntries: defaults,
		}
	}
	return out
}

// ParseRemoteNfs4Dump consumes concatenated `nfs4_getfacl` output prefixed by
// `# file: <path>` lines emitted from the remote driver script.
func ParseRemoteNfs4Dump(raw string) map[string]*models.ACL {
	out := make(map[string]*models.ACL)
	for _, stanza := range splitStanzas(raw) {
		path, body := extractPathAndBody(stanza)
		if path == "" {
			continue
		}
		entries := parseNfsV4ACL(body)
		if len(entries) == 0 {
			continue
		}
		out[path] = &models.ACL{
			Type:         "nfsv4",
			NfsV4Entries: entries,
		}
	}
	return out
}

func splitStanzas(raw string) []string {
	var stanzas []string
	var cur []string
	flush := func() {
		if len(cur) > 0 {
			stanzas = append(stanzas, strings.Join(cur, "\n"))
			cur = cur[:0]
		}
	}
	for _, line := range strings.Split(raw, "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "# file:") {
			flush()
		}
		if strings.TrimSpace(line) == "" {
			flush()
			continue
		}
		cur = append(cur, line)
	}
	flush()
	return stanzas
}

func extractPathAndBody(stanza string) (string, string) {
	lines := strings.Split(stanza, "\n")
	var path string
	var body []string
	for _, line := range lines {
		t := strings.TrimSpace(line)
		if strings.HasPrefix(t, "# file:") {
			path = strings.TrimSpace(strings.TrimPrefix(t, "# file:"))
			continue
		}
		body = append(body, line)
	}
	return path, strings.Join(body, "\n")
}
