// Load-signal classifier for the AdaptiveBatcher.Observe path
// (v0.29.6).
//
// AIMD should only halve on real overload signals. 4xx-other-than-413
// is misuse / wrong-shape / config — halving the batch makes no sense
// and muddies the diagnostic.
package client

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestIsLoadSignal_NilNotLoad(t *testing.T) {
	if IsLoadSignal(nil) {
		t.Error("nil error reported as load signal")
	}
}

func TestIsLoadSignal_RetryableYes(t *testing.T) {
	err := retryableError{fmt.Errorf("server hiccup")}
	if !IsLoadSignal(err) {
		t.Error("retryableError should report as load signal")
	}
}

func TestIsLoadSignal_TerminalNo(t *testing.T) {
	err := terminalError{fmt.Errorf("status 422: bad shape")}
	if IsLoadSignal(err) {
		t.Error("terminalError (4xx misuse) should NOT report as load signal")
	}
}

func TestIsLoadSignal_PayloadTooLargeYes(t *testing.T) {
	err := payloadTooLargeError{fmt.Errorf("status 413: too big")}
	if !IsLoadSignal(err) {
		t.Error("payloadTooLargeError (413) should report as load signal")
	}
}

func TestIsLoadSignal_WrappedThroughRetryExhaustion(t *testing.T) {
	// SendBatch wraps the final attempt's error with fmt.Errorf %w
	// when retries exhaust. IsLoadSignal must walk the chain.
	inner := retryableError{fmt.Errorf("status 500: oops")}
	wrapped := fmt.Errorf("send batch: %d attempts failed: %w", 4, inner)
	if !IsLoadSignal(wrapped) {
		t.Error("wrapped retryableError lost its load-signal identity")
	}
}

func TestIsLoadSignal_WrappedThroughOtherError(t *testing.T) {
	// A wrapped terminalError must still NOT register as load signal.
	inner := terminalError{fmt.Errorf("status 422: bad shape")}
	wrapped := fmt.Errorf("scan run: %w", inner)
	if IsLoadSignal(wrapped) {
		t.Error("wrapped terminalError should NOT report as load signal")
	}
}

func TestIsLoadSignal_PlainSentinelNo(t *testing.T) {
	// A plain errors.New that doesn't carry our types is treated as
	// a non-load-signal (the conservative default).
	if IsLoadSignal(errors.New("random error")) {
		t.Error("plain errors.New should not be a load signal")
	}
}

// Integration: sendOnce returns the right typed error for each status.
func TestSendOnce_StatusCodeClassification(t *testing.T) {
	cases := []struct {
		status   int
		wantLoad bool
		wantKind string
	}{
		{500, true, "retryableError"},
		{502, true, "retryableError"},
		{503, true, "retryableError"},
		{413, true, "payloadTooLargeError"},
		{422, false, "terminalError"},
		{400, false, "terminalError"},
		{401, false, "terminalError"},
		{403, false, "terminalError"},
	}
	for _, tc := range cases {
		t.Run(http.StatusText(tc.status), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
			}))
			defer srv.Close()
			c := New(srv.URL, "k")
			err := c.sendOnce(context.Background(), []byte("{}"))
			if err == nil {
				t.Fatalf("expected error for status %d", tc.status)
			}
			got := IsLoadSignal(err)
			if got != tc.wantLoad {
				t.Errorf("status %d: IsLoadSignal=%v, want=%v; err=%v",
					tc.status, got, tc.wantLoad, err)
			}
		})
	}
}
