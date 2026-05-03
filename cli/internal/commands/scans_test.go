package commands

import (
	"errors"
	"testing"

	"github.com/akashic-project/akashic/cli/internal/client"
)

// classifyAPIError translates the API client's status-bearing errors
// into the meaningful exit codes documented on `akashic scan` (1 user,
// 2 server). This guards the contract main() relies on.
func TestClassifyAPIError(t *testing.T) {
	tests := []struct {
		name string
		in   error
		want int
	}{
		{"4xx → user", &client.APIError{Status: 404}, ExitUserErr},
		{"5xx → server", &client.APIError{Status: 503}, ExitServerErr},
		{"plain → server", errors.New("network ouch"), ExitServerErr},
		{"nil → no exit", nil, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := classifyAPIError(tt.in)
			if tt.want == 0 {
				if err != nil {
					t.Fatalf("expected nil, got %v", err)
				}
				return
			}
			var ee *ExitErr
			if !errors.As(err, &ee) {
				t.Fatalf("expected *ExitErr, got %T %v", err, err)
			}
			if ee.Code != tt.want {
				t.Errorf("code: got %d, want %d", ee.Code, tt.want)
			}
		})
	}
}
