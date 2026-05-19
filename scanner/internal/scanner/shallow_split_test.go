// Budgeted shallow-split mode coverage (v0.34.0).
//
// When Options.ShallowSplit is set, Run walks the subtree breadth-first
// over a local frontier queue up to ShallowBudget entries, emitting each
// directory's own record + its files, then hands the un-walked frontier
// back via ShallowSplit so the caller can enqueue it as fresh work
// units. These tests pin that contract against a real LocalConnector.
package scanner

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fanoutTree builds root/{a.txt, d1/x.txt, d2/y.txt, d3/z.txt}.
func fanoutTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.txt"), []byte("a"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, d := range []struct{ sub, file string }{
		{"d1", "x.txt"}, {"d2", "y.txt"}, {"d3", "z.txt"},
	} {
		if err := os.MkdirAll(filepath.Join(dir, d.sub), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, d.sub, d.file), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// runShallow runs Run in shallow-split mode and returns the frontier
// handed to ShallowSplit plus every file basename emitted.
func runShallow(t *testing.T, root string, budget int) (frontier []string, files []string) {
	t.Helper()
	srv := newTestServer(t, func(b models.ScanBatch) {
		for _, e := range b.Entries {
			if e.Kind == "file" {
				files = append(files, filepath.Base(e.Path))
			}
		}
	})
	defer srv.Close()

	s := New(client.New(srv.URL, "k"), connector.NewLocalConnector(), Options{
		SourceID: "src", ScanID: "scan", Root: root, BatchSize: 100,
		SuppressScanFinal: true,
		ShallowBudget:     budget,
		ShallowSplit: func(_ context.Context, f []string) error {
			frontier = append([]string{}, f...)
			return nil
		},
	})
	if _, err := s.Run(context.Background()); err != nil {
		t.Fatalf("Run: %v", err)
	}
	sort.Strings(frontier)
	sort.Strings(files)
	return frontier, files
}

func TestShallowSplit_SubtreeUnderBudget_NoSplit(t *testing.T) {
	// A generous budget walks the whole tree in one unit — the frontier
	// handed to ShallowSplit is empty (nothing to enqueue).
	frontier, files := runShallow(t, fanoutTree(t), 1000)
	if len(frontier) != 0 {
		t.Errorf("under-budget walk should split nothing, got frontier %v", frontier)
	}
	if got := strings.Join(files, ","); got != "a.txt,x.txt,y.txt,z.txt" {
		t.Errorf("under-budget walk should emit every file, got %q", got)
	}
}

func TestShallowSplit_OverBudget_SplitsOverflowFrontier(t *testing.T) {
	// Budget 2 is spent walking root (its directory record + a.txt), so
	// the three subdirectories are handed back as the frontier and their
	// files are NOT walked by this unit.
	frontier, files := runShallow(t, fanoutTree(t), 2)
	if got := strings.Join(frontier, ","); got != "d1,d2,d3" {
		t.Errorf("over-budget walk should split the un-walked subdirs, got %q", got)
	}
	if got := strings.Join(files, ","); got != "a.txt" {
		t.Errorf("over-budget walk should emit only root files, got %q", got)
	}
}

func TestShallowSplit_FrontierPathsAreRelativeToRoot(t *testing.T) {
	// Frontier entries are plain relative paths (the unit runner joins
	// them onto the unit's own path to form scan-root-relative units).
	frontier, _ := runShallow(t, fanoutTree(t), 2)
	for _, p := range frontier {
		if filepath.IsAbs(p) || strings.Contains(p, string(filepath.Separator)) {
			t.Errorf("frontier path %q should be a bare relative name", p)
		}
	}
}
