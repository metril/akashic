package nfsprobe

import "testing"

// MOUNT3 EXPORT reply parsing. Encode a representative shape and
// confirm the parser recovers it. RFC 1813's mount.x:
//
//   typedef exportnode *exports;
//   struct exportnode { name ex_dir; groups ex_groups; exports ex_next; }
//
// Wire shape: linked-list of [present(bool), path, groups[], next...]
// terminated by a present=false marker. groups themselves use the
// same present-prefixed list shape.

func encodeExportReply(entries []MountExportEntry) []byte {
	w := newXDRWriter()
	for _, e := range entries {
		w.writeBool(true)
		w.writeString(e.Path)
		for _, g := range e.Groups {
			w.writeBool(true)
			w.writeString(g)
		}
		w.writeBool(false) // end of groups
	}
	w.writeBool(false) // end of exports
	return w.bytes()
}

func TestParseExportReplyEmpty(t *testing.T) {
	out, err := parseExportReply(encodeExportReply(nil))
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 0 {
		t.Errorf("got %d entries, want 0", len(out))
	}
}

func TestParseExportReplySingleNoGroups(t *testing.T) {
	in := []MountExportEntry{{Path: "/srv/data", Groups: nil}}
	out, err := parseExportReply(encodeExportReply(in))
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 1 || out[0].Path != "/srv/data" {
		t.Errorf("got %+v", out)
	}
	if len(out[0].Groups) != 0 {
		t.Errorf("groups: got %v", out[0].Groups)
	}
}

func TestParseExportReplyMultipleWithGroups(t *testing.T) {
	in := []MountExportEntry{
		{Path: "/srv/a", Groups: []string{"*"}},
		{Path: "/srv/b", Groups: []string{"10.0.0.0/8", "host1"}},
		{Path: "/srv/c", Groups: nil},
	}
	out, err := parseExportReply(encodeExportReply(in))
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 3 {
		t.Fatalf("got %d entries", len(out))
	}
	if out[1].Path != "/srv/b" || len(out[1].Groups) != 2 ||
		out[1].Groups[0] != "10.0.0.0/8" || out[1].Groups[1] != "host1" {
		t.Errorf("entry 1: %+v", out[1])
	}
	if out[2].Path != "/srv/c" || len(out[2].Groups) != 0 {
		t.Errorf("entry 2: %+v", out[2])
	}
}

func TestParseExportReplyTruncatedReportsError(t *testing.T) {
	// Truncated body — first present=true bool but no path.
	body := []byte{0, 0, 0, 1}
	_, err := parseExportReply(body)
	if err == nil {
		t.Fatal("expected error on truncated reply")
	}
}

func TestMountStatusName(t *testing.T) {
	cases := map[uint32]string{
		mnt3Ok:        "OK",
		mnt3ErrAccess: "MNT3ERR_ACCES",
		mnt3ErrNoEnt:  "MNT3ERR_NOENT",
	}
	for code, want := range cases {
		if got := mountStatusName(code); got != want {
			t.Errorf("status %d: got %q, want %q", code, got, want)
		}
	}
}
