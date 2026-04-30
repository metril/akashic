package metadata

import "io/fs"

// POSIX setuid/setgid/sticky bits, as they appear in st_mode. Go's
// fs.FileMode keeps the same SEMANTIC bits but at different numeric
// positions (bit 25, 24, 17) — translating to POSIX positions is the
// whole job of SafeMode below.
const (
	posixSetuid uint32 = 0o4000
	posixSetgid uint32 = 0o2000
	posixSticky uint32 = 0o1000
)

// SafeMode returns the file's mode bits in POSIX st_mode form, capped
// to the 12 bits that fit comfortably inside the api side's INT32
// `mode` column.
//
// Why this helper exists: Go's fs.FileMode encodes type classification
// (os.ModeDir = 1<<31, os.ModeSymlink = 1<<28, …) in the high bits of
// a uint32. Casting info.Mode() to uint32 directly produces values
// like 2_147_484_159 for a directory — well over INT32's ceiling,
// which makes asyncpg reject every row with "value out of int32
// range" and the entire scan batch 500s.
//
// We also can't just bitwise-AND with `fs.ModeSetuid | fs.ModeSetgid |
// fs.ModeSticky`: Go places those flags at bit positions 25 / 24 / 17,
// not at the POSIX positions 11 / 10 / 9. Preserving them in their
// Go positions would produce st_mode values like 0o40000755 — still
// under INT32 max, but semantically wrong on the wire. We translate
// each one to its POSIX bit explicitly.
//
// Type bits (regular / dir / symlink / fifo / …) are deliberately
// dropped: the entries table has a separate `kind` column for that
// discriminator, so no downstream consumer reads them out of `mode`.
//
// The Linux walker has a separate fast path that pulls the real
// 16-bit st_mode straight out of syscall.Stat_t (see collector.go) —
// SafeMode is the fallback for every code path that only has a plain
// fs.FileInfo (remote connectors, non-Linux test environments).
func SafeMode(info fs.FileInfo) uint32 {
	m := info.Mode()
	out := uint32(m.Perm())
	if m&fs.ModeSetuid != 0 {
		out |= posixSetuid
	}
	if m&fs.ModeSetgid != 0 {
		out |= posixSetgid
	}
	if m&fs.ModeSticky != 0 {
		out |= posixSticky
	}
	return out
}
