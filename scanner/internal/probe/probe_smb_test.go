// SMB probe error classification + guest-rejection guard (v0.29.1).
//
// The pre-v0.29.1 SMB probe accepted guest/anonymous session downgrades
// as "reachable" — when the user's NTLM credentials didn't match and
// the server (Samba or older Windows) had "fall back to guest"
// enabled, the connector silently received a SUCCESSFUL session with
// the IS_GUEST flag set and Connect returned nil. That's the bug the
// user reported as "credentials supplied won't work but the test says
// it can reach."
//
// We can't reproduce the full SMB flow in a unit test without a real
// server, but we CAN:
//   * Verify the config-step short-circuits fire (missing host /
//     user / share).
//   * Verify the error classifier maps connector error prefixes to
//     the correct probe step — including the new guest-rejection
//     message landing on step=auth.
package probe

import (
	"context"
	"errors"
	"testing"
)

func TestSMBRequiresConfigFields(t *testing.T) {
	ctx := context.Background()
	cases := []struct {
		name string
		cfg  map[string]any
	}{
		{"no host", map[string]any{"username": "u", "share": "s"}},
		{"no user", map[string]any{"host": "h", "share": "s"}},
		{"no share", map[string]any{"host": "h", "username": "u"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := runSMB(ctx, c.cfg)
			if r.OK {
				t.Fatalf("expected ok=false; got %+v", r)
			}
			if r.Step != "config" {
				t.Errorf("expected step=config; got step=%q error=%q", r.Step, r.Error)
			}
		})
	}
}

func TestClassifySMBProbeError(t *testing.T) {
	cases := []struct {
		err      string
		wantStep string
		wantMsg  string
	}{
		{
			err:      "smb dial 10.0.0.1:445: i/o timeout",
			wantStep: "connect",
			wantMsg:  "10.0.0.1:445: i/o timeout",
		},
		{
			err:      "smb session: NTLMSSP server returned 0xc000006d",
			wantStep: "auth",
			wantMsg:  "NTLMSSP server returned 0xc000006d",
		},
		{
			// v0.29.1 guest-rejection error from the connector. Classifies
			// as step=auth and the full diagnostic survives in the message
			// so the panel renders the explanation.
			err:      `smb session: server fell back to guest session for user "alice" — supplied credentials were rejected (configure the server to deny guest fallback if you need to detect this earlier)`,
			wantStep: "auth",
			wantMsg:  `server fell back to guest session for user "alice" — supplied credentials were rejected (configure the server to deny guest fallback if you need to detect this earlier)`,
		},
		{
			err:      `smb session: server returned an anonymous (NULL) session for user "alice" — supplied credentials were rejected`,
			wantStep: "auth",
			wantMsg:  `server returned an anonymous (NULL) session for user "alice" — supplied credentials were rejected`,
		},
		{
			// v0.29.6 share-ACL ReadDir-smoke rejection. The
			// connector mounts the share successfully (tree connect
			// permitted), then a ReadDir(".") at the share root
			// returns ACCESS_DENIED — credentials authenticate but
			// don't grant list permission. Classifies as step=auth
			// and the full diagnostic survives in the message.
			err:      `smb session: share "public" mounted but ReadDir denied (credentials lack list permission for user "alice": STATUS_ACCESS_DENIED)`,
			wantStep: "auth",
			wantMsg:  `share "public" mounted but ReadDir denied (credentials lack list permission for user "alice": STATUS_ACCESS_DENIED)`,
		},
		{
			err:      "smb mount \\\\h\\share: STATUS_ACCESS_DENIED",
			wantStep: "mount",
			wantMsg:  "\\\\h\\share: STATUS_ACCESS_DENIED",
		},
		{
			err:      "something unexpected",
			wantStep: "connect",
			wantMsg:  "something unexpected",
		},
	}
	for _, c := range cases {
		t.Run(c.err, func(t *testing.T) {
			step, msg := classifySMBProbeError(errors.New(c.err))
			if step != c.wantStep || msg != c.wantMsg {
				t.Errorf("got (step=%q, msg=%q); want (step=%q, msg=%q)",
					step, msg, c.wantStep, c.wantMsg)
			}
		})
	}
}

// TestSMBNoServerFailsAtConnect proves the probe surfaces transport
// failure as step=connect, not the previous step=auth catch-all. Uses
// a reserved 240.0.0.0/4 address that won't route — same trick as
// TestNFSConnectsAtRealHost.
//
// v0.29.5 — supply a password so the test exercises the connector
// path (the new config-step guard would otherwise short-circuit
// before TCP dial).
func TestSMBNoServerFailsAtConnect(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*1e9)
	defer cancel()
	r := runSMB(ctx, map[string]any{
		"host":     "240.0.0.1",
		"username": "alice",
		"password": "irrelevant-since-tcp-will-fail",
		"share":    "test",
	})
	if r.OK {
		t.Fatalf("expected ok=false against unreachable host; got %+v", r)
	}
	if r.Step != "connect" {
		t.Errorf("expected step=connect on transport failure; got step=%q error=%q",
			r.Step, r.Error)
	}
}
