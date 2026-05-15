// SMB empty-password rejection (v0.29.5).
//
// Pre-fix runSMB only validated host/user/share — empty password
// sailed through to go-smb2's NTLMInitiator{User: "alice", Password: ""},
// which some servers accept as a fully AUTHENTICATED session (not
// guest) against a null-password account. The v0.29.1 IsGuest /
// IsAnonymous check never fired and the probe lands ok=true against
// credentials the user knew were wrong. v0.29.5 catches this at the
// probe layer.
package probe

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestSMB_EmptyPassword_RejectedAsConfig(t *testing.T) {
	res := runSMB(context.Background(), map[string]any{
		"host":     "smb.example.com",
		"username": "alice",
		"share":    "share1",
		// "password" deliberately absent (== "").
	})
	if res.OK {
		t.Fatalf("expected ok=false on empty password; got %+v", res)
	}
	if res.Step != "config" {
		t.Errorf("expected step=config; got step=%q error=%q", res.Step, res.Error)
	}
	if !strings.Contains(res.Error, "password required") {
		t.Errorf("error should mention 'password required'; got %q", res.Error)
	}
}

func TestSMB_EmptyPassword_AllowedWhenOptInSet(t *testing.T) {
	// With allow_empty_password=true the probe bypasses the config
	// guard and proceeds to the connector. We hit a real Connect()
	// here that'll fail at TCP dial (no real server at that address),
	// so the result should be ok=false but with step NOT config —
	// confirming the guard was bypassed and the connector ran.
	//
	// Bounded ctx so the test doesn't wait for the OS TCP timeout
	// when the connector tries to dial the reserved 240.0.0.0/4
	// address. 3 s is plenty for the dial-timeout path.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	res := runSMB(ctx, map[string]any{
		"host":                 "240.0.0.1", // reserved, won't route
		"username":             "alice",
		"share":                "share1",
		"allow_empty_password": true,
	})
	if res.OK {
		t.Fatalf("unreachable host should fail; got %+v", res)
	}
	if res.Step == "config" {
		t.Errorf("allow_empty_password=true should bypass config guard; got step=%q error=%q",
			res.Step, res.Error)
	}
}

func TestSMB_EmptyPassword_AllowOptInAcceptsStringForm(t *testing.T) {
	// boolish() tolerates string-form bools ("true"/"1"/"yes") so a
	// client that JSON-encoded the flag as a string instead of a
	// native bool still opts in correctly.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	res := runSMB(ctx, map[string]any{
		"host":                 "240.0.0.1",
		"username":             "alice",
		"share":                "share1",
		"allow_empty_password": "true",
	})
	if res.Step == "config" {
		t.Errorf("string-form allow_empty_password should bypass config guard; "+
			"got step=%q error=%q", res.Step, res.Error)
	}
}
