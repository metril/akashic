package connector

import (
	"errors"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/internal/metadata/sddl"
)

// mockSDFetcher implements sdFetcher for unit tests without a live SMB server.
type mockSDFetcher struct {
	data map[string][]byte
	err  error
}

func (m *mockSDFetcher) GetSecurityDescriptorBytes(path string) ([]byte, error) {
	if m.err != nil {
		return nil, m.err
	}
	if b, ok := m.data[path]; ok {
		return b, nil
	}
	return nil, errors.New("mock: path not found")
}

// buildTestSD builds a minimal self-relative NT SD with one DACL Allow ACE.
// Owner = S-1-5-18 (SYSTEM), DACL grants GENERIC_ALL to S-1-5-32-544 (Admins).
func buildTestSD() []byte {
	owner := sddl.BuildSID(5, 18)
	group := sddl.BuildSID(5, 32, 544)
	ace := sddl.BuildACE(0x00 /*allow*/, 0x00, 0x10000000 /*GENERIC_ALL*/, sddl.BuildSID(5, 32, 544))
	dacl := sddl.BuildACL(ace)
	// control: DACL_PRESENT (0x04) | SELF_RELATIVE (0x8000)
	return sddl.BuildSecurityDescriptor(0x8004, owner, group, dacl)
}

func TestQuerySecurityDescriptor_ReturnsRawBytes(t *testing.T) {
	sd := buildTestSD()
	c := &SMBConnector{
		sdSource: &mockSDFetcher{data: map[string][]byte{"share\\file.txt": sd}},
		resolver: metadata.NewSIDResolver(nil),
	}

	got, err := c.querySecurityDescriptor("share\\file.txt")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got) == 0 {
		t.Fatal("expected non-empty SD bytes")
	}
	if got[0] != 1 {
		t.Errorf("SD revision: got %d want 1", got[0])
	}
}

func TestQuerySecurityDescriptor_NotConnected(t *testing.T) {
	c := &SMBConnector{sdSource: nil}
	_, err := c.querySecurityDescriptor("any/path")
	if err == nil {
		t.Fatal("expected error when not connected")
	}
}

func TestQuerySecurityDescriptor_PropagatesServerError(t *testing.T) {
	serverErr := errors.New("access denied")
	c := &SMBConnector{
		sdSource: &mockSDFetcher{err: serverErr},
		resolver: metadata.NewSIDResolver(nil),
	}
	_, err := c.querySecurityDescriptor("share\\protected.txt")
	if err == nil {
		t.Fatal("expected error from server")
	}
	if !errors.Is(err, serverErr) {
		t.Errorf("error chain: got %v, want to contain %v", err, serverErr)
	}
}

func TestQuerySecurityDescriptor_IntegrationWithSDToNtACL(t *testing.T) {
	sd := buildTestSD()
	c := &SMBConnector{
		sdSource: &mockSDFetcher{data: map[string][]byte{"dir\\doc.txt": sd}},
		resolver: metadata.NewSIDResolver(nil),
	}

	raw, err := c.querySecurityDescriptor("dir\\doc.txt")
	if err != nil {
		t.Fatalf("querySecurityDescriptor: %v", err)
	}

	acl, err := metadata.SDToNtACL(raw, c.resolver)
	if err != nil {
		t.Fatalf("SDToNtACL: %v", err)
	}
	if acl.Type != "nt" {
		t.Errorf("ACL type: got %q want \"nt\"", acl.Type)
	}
	if acl.Owner == nil || acl.Owner.Sid == "" {
		t.Error("expected non-empty owner SID")
	}
	if len(acl.NtEntries) == 0 {
		t.Error("expected at least one DACL ACE")
	}
}
