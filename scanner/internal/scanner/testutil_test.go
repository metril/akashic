package scanner

import (
	"testing"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/scanner/internal/client"
)

var (
	testSource = uuid.MustParse("11111111-1111-4111-8111-111111111111")
	testScan   = uuid.MustParse("22222222-2222-4222-8222-222222222222")
)

func mustClient(t *testing.T, baseURL, key string) *client.Client {
	t.Helper()
	c, err := client.New(baseURL, key)
	if err != nil {
		t.Fatal(err)
	}
	return c
}
