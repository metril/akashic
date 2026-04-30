package nfsprobe

import (
	"bytes"
	"testing"
)

func TestAuthNoneIsEmpty(t *testing.T) {
	c := noAuth.cred()
	if c.flavor != authNone || len(c.body) != 0 {
		t.Errorf("AUTH_NONE cred: %+v", c)
	}
	v := noAuth.verifier()
	if v.flavor != authNone || len(v.body) != 0 {
		t.Errorf("AUTH_NONE verifier: %+v", v)
	}
}

func TestAuthSysCredEncoding(t *testing.T) {
	a := newAuthSys("host1", 1000, 1000, []uint32{27, 100})
	a.stamp = 0xCAFEF00D // pin for deterministic comparison
	cred := a.cred()
	if cred.flavor != authSys {
		t.Errorf("flavor: got %d want AUTH_SYS", cred.flavor)
	}
	// Decode the body and confirm fields.
	r := newXDRReader(cred.body)
	stamp, _ := r.readUint32()
	if stamp != 0xCAFEF00D {
		t.Errorf("stamp: got %x", stamp)
	}
	machine, _ := r.readString()
	if machine != "host1" {
		t.Errorf("machine: got %q", machine)
	}
	uid, _ := r.readUint32()
	if uid != 1000 {
		t.Errorf("uid: got %d", uid)
	}
	gid, _ := r.readUint32()
	if gid != 1000 {
		t.Errorf("gid: got %d", gid)
	}
	count, _ := r.readUint32()
	if count != 2 {
		t.Errorf("aux count: got %d", count)
	}
	g0, _ := r.readUint32()
	g1, _ := r.readUint32()
	if g0 != 27 || g1 != 100 {
		t.Errorf("aux gids: got %d, %d", g0, g1)
	}
}

func TestAuthSysVerifierIsNone(t *testing.T) {
	a := newAuthSys("h", 0, 0, nil)
	v := a.verifier()
	if v.flavor != authNone {
		t.Errorf("AUTH_SYS verifier should be AUTH_NONE, got flavor %d", v.flavor)
	}
}

func TestAuthSysAuxGIDsCappedAt16(t *testing.T) {
	gids := make([]uint32, 20)
	for i := range gids {
		gids[i] = uint32(i)
	}
	a := newAuthSys("h", 0, 0, gids)
	if len(a.auxGids) != 16 {
		t.Errorf("aux GID truncation: got %d, want 16", len(a.auxGids))
	}
}

func TestAuthBodyOnWireRoundTrip(t *testing.T) {
	// writeAuthBody → readAuthBody preserves both flavor and body.
	w := newXDRWriter()
	writeAuthBody(w, authBody{flavor: 7, body: []byte{0xAA, 0xBB, 0xCC}})
	r := newXDRReader(w.bytes())
	a, err := readAuthBody(r)
	if err != nil {
		t.Fatal(err)
	}
	if a.flavor != 7 {
		t.Errorf("flavor: %d", a.flavor)
	}
	if !bytes.Equal(a.body, []byte{0xAA, 0xBB, 0xCC}) {
		t.Errorf("body: %x", a.body)
	}
}
