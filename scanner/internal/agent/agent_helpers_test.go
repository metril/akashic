package agent

import (
	"context"
	"encoding/base64"
)

// Tiny shim — newContextHelper returns a cancellable context as the
// concrete `*context.cancelCtx` type, but we expose it via a
// generic-enough signature for the test file. Putting it in a
// dedicated _test.go avoids polluting the package's import surface
// with `context` for non-test builds.
type ctxlike = context.Context

func newContextHelper() (ctxlike, context.CancelFunc) {
	return context.WithCancel(context.Background())
}

func decodeBase64URL(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(s)
}
