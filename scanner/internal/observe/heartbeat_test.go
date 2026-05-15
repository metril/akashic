package observe

import (
	"strings"
	"testing"
)

// v0.29.8 — decodeCancelMessage routes the 409 body's reason field to
// the right log line so we stop mis-attributing watchdog reaps and
// terminal-complete races as "cancelled by user".
func TestDecodeCancelMessage(t *testing.T) {
	cases := []struct {
		name     string
		body     string
		wantSub  string
	}{
		{
			name:    "user reason",
			body:    `{"detail": {"status": "cancelled", "reason": "user", "message": "scan is cancelled"}}`,
			wantSub: "cancelled by user",
		},
		{
			name:    "watchdog reason",
			body:    `{"detail": {"status": "failed", "reason": "watchdog", "message": "scan is failed"}}`,
			wantSub: "watchdog",
		},
		{
			name:    "completed reason",
			body:    `{"detail": {"status": "completed", "reason": "completed", "message": "scan is completed"}}`,
			wantSub: "scan completed",
		},
		{
			name:    "failed reason with cause",
			body:    `{"detail": {"status": "failed", "reason": "failed:oom", "message": "scan is failed"}}`,
			wantSub: "failed:oom",
		},
		{
			name:    "empty reason falls back to user",
			body:    `{"detail": {"status": "cancelled", "reason": "", "message": "scan is cancelled"}}`,
			wantSub: "cancelled by user",
		},
		{
			name:    "missing reason key falls back to user",
			body:    `{"detail": {"status": "cancelled", "message": "scan is cancelled"}}`,
			wantSub: "cancelled by user",
		},
		{
			name:    "legacy plain-string detail (pre-v0.29.8 API) falls back",
			body:    `{"detail": "scan is cancelled"}`,
			wantSub: "cancelled by user",
		},
		{
			name:    "malformed JSON falls back",
			body:    `not json at all`,
			wantSub: "cancelled by user",
		},
		{
			name:    "empty body falls back",
			body:    ``,
			wantSub: "cancelled by user",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := decodeCancelMessage(strings.NewReader(tc.body))
			if !strings.Contains(got, tc.wantSub) {
				t.Errorf("decodeCancelMessage(%q) = %q; want substring %q",
					tc.body, got, tc.wantSub)
			}
			if !strings.HasSuffix(got, "exiting") {
				t.Errorf("decodeCancelMessage(%q) = %q; want suffix 'exiting'",
					tc.body, got)
			}
		})
	}
}
