package samr

import (
	"errors"
	"testing"
)

func TestParseSidString_RoundTrip(t *testing.T) {
	in := "S-1-5-21-1004336348-1177238915-682003330-1013"
	s, err := ParseSidString(in)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if got := s.String(); got != in {
		t.Fatalf("round-trip: got %q want %q", got, in)
	}
	if s.Revision != 1 {
		t.Errorf("revision = %d, want 1", s.Revision)
	}
	if len(s.SubAuthority) != 5 {
		t.Errorf("sub-auth count = %d, want 5 (21,A,B,C,RID)", len(s.SubAuthority))
	}
}

func TestParseSidString_AuthorityIsBigEndian(t *testing.T) {
	// "5" decodes to authority bytes [0,0,0,0,0,5] (big-endian 48-bit).
	s, err := ParseSidString("S-1-5-32")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	want := [6]byte{0, 0, 0, 0, 0, 5}
	if s.Authority != want {
		t.Fatalf("authority = %v, want %v", s.Authority, want)
	}
}

func TestParseSidString_Invalid(t *testing.T) {
	cases := []string{
		"",
		"foo",
		"S-",
		"S-1",
		"S-not-a-number",
		"S-1-5-not-a-sub-auth",
	}
	for _, in := range cases {
		if _, err := ParseSidString(in); err == nil {
			t.Errorf("ParseSidString(%q) = nil err, want error", in)
		}
	}
}

func TestSplitDomainAndRid(t *testing.T) {
	s, _ := ParseSidString("S-1-5-21-1004336348-1177238915-682003330-1013")
	dom, rid, err := SplitDomainAndRid(s)
	if err != nil {
		t.Fatalf("split: %v", err)
	}
	if rid != 1013 {
		t.Errorf("rid = %d, want 1013", rid)
	}
	if got := dom.String(); got != "S-1-5-21-1004336348-1177238915-682003330" {
		t.Errorf("domain SID = %q", got)
	}
	if len(dom.SubAuthority) != 4 {
		t.Errorf("domain sub-auth count = %d, want 4 (21,A,B,C — drops RID)", len(dom.SubAuthority))
	}
}

func TestSplitDomainAndRid_NoSubAuth(t *testing.T) {
	_, _, err := SplitDomainAndRid(SID{Revision: 1})
	if !errors.Is(err, ErrInvalidSID) {
		t.Fatalf("err = %v, want ErrInvalidSID", err)
	}
}
