package metadata

import "testing"

func TestWellKnownSID_Resolves(t *testing.T) {
	cases := map[string]string{
		"S-1-1-0":      "Everyone",
		"S-1-5-18":     "NT AUTHORITY\\SYSTEM",
		"S-1-5-32-544": "BUILTIN\\Administrators",
		"S-1-5-11":     "NT AUTHORITY\\Authenticated Users",
	}
	for sid, want := range cases {
		got := WellKnownSIDName(sid)
		if got != want {
			t.Errorf("sid %s: got %q want %q", sid, got, want)
		}
	}
}

func TestWellKnownSID_Unknown(t *testing.T) {
	if WellKnownSIDName("S-1-5-21-1-2-3-1234") != "" {
		t.Error("expected empty for unknown domain SID")
	}
}
