package main

import (
	"encoding/json"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/lsarpc"
)

func TestValidateResolveNameArgs(t *testing.T) {
	cases := []struct {
		typ, host, user, name string
		ok                    bool
	}{
		{"smb", "h", "u", "jdoe", true},
		{"smb", "h", "u", `DOM\jdoe`, true},
		{"smb", "h", "u", "jdoe@dom", true},
		{"nfs", "h", "u", "jdoe", false},
		{"smb", "", "u", "jdoe", false},
		{"smb", "h", "", "jdoe", false},
		{"smb", "h", "u", "  ", false},
	}
	for _, c := range cases {
		if err := validateResolveNameArgs(c.typ, c.host, c.user, c.name); (err == nil) != c.ok {
			t.Errorf("%+v: err=%v", c, err)
		}
	}
}

func TestResolveNameOutputJSON(t *testing.T) {
	b, _ := json.Marshal(resolveNameOutput(lsarpc.NameResult{
		Mapped: true, SidType: lsarpc.SidTypeUser, SidString: "S-1-5-21-1-2-3-1104", Domain: "DOM", DomainSid: "S-1-5-21-1-2-3",
	}))
	want := `{"sid":"S-1-5-21-1-2-3-1104","domain":"DOM","domain_sid":"S-1-5-21-1-2-3","kind":"user"}`
	if string(b) != want {
		t.Fatalf("got %s", b)
	}
}

func TestClassifyResolvedName(t *testing.T) {
	u := func(sid, dom string, typ uint16) lsarpc.NameResult {
		return lsarpc.NameResult{Mapped: true, SidType: typ, SidString: sid, Domain: dom}
	}
	cases := []struct {
		name   string
		r      lsarpc.NameResult
		domain string
		ok     bool
	}{
		{"user", u("S-1-5-21-1-2-3-1104", "DOM", lsarpc.SidTypeUser), "", true},
		{"user domain match ci", u("S-1-5-21-1-2-3-1104", "DOM", lsarpc.SidTypeUser), "dom", true},
		{"user domain mismatch", u("S-1-5-21-1-2-3-1104", "NAS", lsarpc.SidTypeUser), "DOM", false},
		{"unmapped", lsarpc.NameResult{}, "", false},
		{"group", u("S-1-5-21-1-2-3-512", "DOM", lsarpc.SidTypeGroup), "", false},
		{"alias builtin", u("S-1-5-32-544", "BUILTIN", lsarpc.SidTypeAlias), "", false},
		{"builtin typed user", u("S-1-5-32-500", "BUILTIN", lsarpc.SidTypeUser), "", false},
		{"well-known", u("S-1-1-0", "", lsarpc.SidTypeWellKnownGroup), "", false},
		{"well-known typed user", u("S-1-5-18", "NT AUTHORITY", lsarpc.SidTypeUser), "", false},
	}
	for _, c := range cases {
		if err := classifyResolvedName(c.r, c.domain); (err == nil) != c.ok {
			t.Errorf("%s: err=%v", c.name, err)
		}
	}
}

func TestQualifyName(t *testing.T) {
	for in, want := range map[string]string{"jdoe": `DOM\jdoe`, `X\jdoe`: `X\jdoe`, "j@d": "j@d"} {
		if got := qualifyName(in, "DOM"); got != want {
			t.Errorf("%s: %s", in, got)
		}
	}
	if qualifyName("jdoe", "") != "jdoe" {
		t.Error("no domain")
	}
}
