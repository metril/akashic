// ref: MS-DTYP

package smb2

import (
	"strconv"
	"strings"
)

type Filetime struct {
	LowDateTime  uint32
	HighDateTime uint32
}

func (ft *Filetime) Size() int {
	return 8
}

func (ft *Filetime) Encode(p []byte) {
	le.PutUint32(p[:4], ft.LowDateTime)
	le.PutUint32(p[4:8], ft.HighDateTime)
}

func (ft *Filetime) Nanoseconds() int64 {
	nsec := int64(ft.HighDateTime)<<32 + int64(ft.LowDateTime)
	nsec -= 116444736000000000
	nsec *= 100
	return nsec
}

func NsecToFiletime(nsec int64) (ft *Filetime) {
	nsec /= 100
	nsec += 116444736000000000

	return &Filetime{
		LowDateTime:  uint32(nsec & 0xffffffff),
		HighDateTime: uint32(nsec >> 32 & 0xffffffff),
	}
}

type FiletimeDecoder []byte

func (ft FiletimeDecoder) LowDateTime() uint32 {
	return le.Uint32(ft[:4])
}

func (ft FiletimeDecoder) HighDateTime() uint32 {
	return le.Uint32(ft[4:8])
}

func (ft FiletimeDecoder) Nanoseconds() int64 {
	nsec := int64(ft.HighDateTime())<<32 + int64(ft.LowDateTime())
	nsec -= 116444736000000000
	nsec *= 100
	return nsec
}

func (ft FiletimeDecoder) Decode() *Filetime {
	return &Filetime{
		LowDateTime:  ft.LowDateTime(),
		HighDateTime: ft.HighDateTime(),
	}
}

type Sid struct {
	Revision            uint8
	IdentifierAuthority uint64
	SubAuthority        []uint32
}

func (sid *Sid) String() string {
	list := make([]string, 0, 3+len(sid.SubAuthority))
	list = append(list, "S")
	list = append(list, strconv.Itoa(int(sid.Revision)))
	if sid.IdentifierAuthority < uint64(1<<32) {
		list = append(list, strconv.FormatUint(sid.IdentifierAuthority, 10))
	} else {
		list = append(list, "0x"+strconv.FormatUint(sid.IdentifierAuthority, 16))
	}
	for _, a := range sid.SubAuthority {
		list = append(list, strconv.FormatUint(uint64(a), 10))
	}
	return strings.Join(list, "-")
}

func (sid *Sid) Size() int {
	return 8 + len(sid.SubAuthority)*4
}

func (sid *Sid) Encode(p []byte) {
	p[0] = sid.Revision
	p[1] = uint8(len(sid.SubAuthority))
	for j := 0; j < 6; j++ {
		p[2+j] = byte(sid.IdentifierAuthority >> uint64(8*(6-j)))
	}
	off := 8
	for _, u := range sid.SubAuthority {
		le.PutUint32(p[off:off+4], u)
		off += 4
	}
}

type SidDecoder []byte

func (c SidDecoder) IsInvalid() bool {
	if len(c) < 8 {
		return true
	}

	if len(c) < 8+int(c.SubAuthorityCount())*4 {
		return true
	}

	return false
}

func (c SidDecoder) Revision() uint8 {
	return c[0]
}

func (c SidDecoder) SubAuthorityCount() uint8 {
	return c[1]
}

func (c SidDecoder) IdentifierAuthority() uint64 {
	var u uint64
	for j := 0; j < 6; j++ {
		u += uint64(c[7-j]) << uint64(8*j)
	}
	return u
}

func (c SidDecoder) SubAuthority() []uint32 {
	count := c.SubAuthorityCount()
	as := make([]uint32, count)
	off := 8
	for i := uint8(0); i < count; i++ {
		as[i] = le.Uint32(c[off : off+4])
		off += 4
	}
	return as
}

func (c SidDecoder) Decode() *Sid {
	return &Sid{
		Revision:            c.Revision(),
		IdentifierAuthority: c.IdentifierAuthority(),
		SubAuthority:        c.SubAuthority(),
	}
}

// SecurityDescriptorDecoder parses a self-relative NT security descriptor
// (MS-DTYP §2.4.6). Added in akashic-acl-capture vendor patch; see
// internal/vendor/go-smb2/PATCHES.md for context.
type SecurityDescriptorDecoder []byte

func (c SecurityDescriptorDecoder) IsInvalid() bool {
	return len(c) < 20 || c[0] != 1
}

func (c SecurityDescriptorDecoder) Revision() uint8 {
	return c[0]
}

func (c SecurityDescriptorDecoder) Control() uint16 {
	return le.Uint16(c[2:4])
}

func (c SecurityDescriptorDecoder) OffsetOwner() uint32 {
	return le.Uint32(c[4:8])
}

func (c SecurityDescriptorDecoder) OffsetGroup() uint32 {
	return le.Uint32(c[8:12])
}

func (c SecurityDescriptorDecoder) OffsetSacl() uint32 {
	return le.Uint32(c[12:16])
}

func (c SecurityDescriptorDecoder) OffsetDacl() uint32 {
	return le.Uint32(c[16:20])
}
