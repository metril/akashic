package metadata

import (
	"io/fs"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// fakeFileInfo lets us test SafeMode against contrived FileMode values
// (e.g., os.ModeDir|0o755) without actually creating a file with those
// exact bits — ModeDir in particular is set automatically by the OS for
// directories, but we want a synthetic case to prove SafeMode strips
// the high-bit flag without depending on platform-specific behavior.
type fakeFileInfo struct {
	mode fs.FileMode
}

func (f fakeFileInfo) Name() string       { return "fake" }
func (f fakeFileInfo) Size() int64        { return 0 }
func (f fakeFileInfo) Mode() fs.FileMode  { return f.mode }
func (f fakeFileInfo) ModTime() time.Time { return time.Time{} }
func (f fakeFileInfo) IsDir() bool        { return f.mode.IsDir() }
func (f fakeFileInfo) Sys() interface{}   { return nil }

// The bug being protected against: Mode() returns os.ModeDir|0o755 ==
// 0x80000_01ED == 2147484141 which overflows INT32. SafeMode must strip
// the os.ModeDir bit and return only 0o755 == 0x1ED.
func TestSafeModeDropsModeDirBit(t *testing.T) {
	got := SafeMode(fakeFileInfo{mode: os.ModeDir | 0o755})
	if got != 0o755 {
		t.Fatalf("ModeDir|0o755: want 0o755 (0x1ED), got 0o%o (0x%X)", got, got)
	}
}

// os.ModeSymlink is another high-bit flag (1<<27) that we must drop.
func TestSafeModeDropsModeSymlinkBit(t *testing.T) {
	got := SafeMode(fakeFileInfo{mode: os.ModeSymlink | 0o644})
	if got != 0o644 {
		t.Fatalf("ModeSymlink|0o644: want 0o644, got 0o%o", got)
	}
}

// setuid / setgid / sticky are real POSIX bits that must SURVIVE the
// mask. They're in the 0o7000 range (bits 9–11) which fs.FileMode
// represents via fs.ModeSetuid / fs.ModeSetgid / fs.ModeSticky.
func TestSafeModePreservesSetuidSetgidSticky(t *testing.T) {
	cases := []struct {
		name string
		in   fs.FileMode
		want uint32
	}{
		{"setuid+0755", fs.ModeSetuid | 0o755, 0o4755},
		{"setgid+0755", fs.ModeSetgid | 0o755, 0o2755},
		{"sticky+0777", fs.ModeSticky | 0o777, 0o1777},
		{"all_three+0755", fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky | 0o755, 0o7755},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := SafeMode(fakeFileInfo{mode: tc.in})
			if got != tc.want {
				t.Errorf("want 0o%o, got 0o%o", tc.want, got)
			}
		})
	}
}

// The whole point of the function: result must always fit INT32 so
// asyncpg doesn't reject the row. 0o7777 == 4095 is the largest
// possible value SafeMode can return (all 12 POSIX bits set), well
// under 2^31-1. Boundary check.
func TestSafeModeAlwaysFitsInt32(t *testing.T) {
	const int32Max uint32 = 1<<31 - 1
	// Throw the worst-case input at it: every Go FileMode flag set.
	worst := fs.FileMode(^uint32(0))
	got := SafeMode(fakeFileInfo{mode: worst})
	if got > int32Max {
		t.Fatalf("SafeMode returned %d which exceeds INT32 max %d", got, int32Max)
	}
	if got > 0o7777 {
		t.Fatalf("SafeMode returned 0o%o which has bits outside the 12-bit POSIX range", got)
	}
}

// Real fs.FileInfo from the OS — confirms the helper works against
// values that didn't come from a fake. Skips on Windows since we
// don't run there.
func TestSafeModeAgainstRealFileInfo(t *testing.T) {
	dir := t.TempDir()
	regular := filepath.Join(dir, "regular.txt")
	if err := os.WriteFile(regular, []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	subdir := filepath.Join(dir, "sub")
	if err := os.Mkdir(subdir, 0o755); err != nil {
		t.Fatal(err)
	}

	fi, err := os.Stat(regular)
	if err != nil {
		t.Fatal(err)
	}
	if got := SafeMode(fi); got&0o777 != 0o644 {
		t.Errorf("regular file: perm bits got 0o%o, want 0o644 lower 9 bits", got)
	}

	di, err := os.Stat(subdir)
	if err != nil {
		t.Fatal(err)
	}
	gotDir := SafeMode(di)
	if gotDir&0o777 != 0o755 {
		t.Errorf("directory: perm bits got 0o%o, want 0o755 lower 9 bits", gotDir)
	}
	if gotDir > 0o7777 {
		t.Errorf("directory: SafeMode leaked high bits (got 0o%o)", gotDir)
	}
}
