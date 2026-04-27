package sddl

import (
	"bytes"
	"encoding/binary"
)

// BuildSID returns the binary form of a SID. Test helper.
func BuildSID(auth uint64, subs ...uint32) []byte {
	var buf bytes.Buffer
	buf.WriteByte(1)
	buf.WriteByte(byte(len(subs)))
	authBytes := make([]byte, 6)
	for i := 5; i >= 0; i-- {
		authBytes[i] = byte(auth & 0xff)
		auth >>= 8
	}
	buf.Write(authBytes)
	for _, s := range subs {
		binary.Write(&buf, binary.LittleEndian, s)
	}
	return buf.Bytes()
}

// BuildACE returns the binary form of an ACE. Test helper.
func BuildACE(aceType, flags byte, mask uint32, sid []byte) []byte {
	body := make([]byte, 0, 8+len(sid))
	body = binary.LittleEndian.AppendUint32(body, mask)
	body = append(body, sid...)
	hdr := make([]byte, 4)
	hdr[0] = aceType
	hdr[1] = flags
	binary.LittleEndian.PutUint16(hdr[2:4], uint16(4+len(body)))
	return append(hdr, body...)
}

// BuildACL returns the binary form of an ACL. Test helper.
func BuildACL(aces ...[]byte) []byte {
	body := bytes.Join(aces, nil)
	hdr := make([]byte, 8)
	hdr[0] = 2
	hdr[1] = 0
	binary.LittleEndian.PutUint16(hdr[2:4], uint16(8+len(body)))
	binary.LittleEndian.PutUint16(hdr[4:6], uint16(len(aces)))
	return append(hdr, body...)
}

// BuildSecurityDescriptor packs owner/group/dacl into a self-relative SD. Test helper.
func BuildSecurityDescriptor(control uint16, owner, group, dacl []byte) []byte {
	var buf bytes.Buffer
	buf.WriteByte(1)
	buf.WriteByte(0)
	binary.Write(&buf, binary.LittleEndian, control)

	offsetsAt := buf.Len()
	for i := 0; i < 4; i++ {
		binary.Write(&buf, binary.LittleEndian, uint32(0))
	}

	var ownerOff, groupOff, daclOff uint32
	if owner != nil {
		ownerOff = uint32(buf.Len())
		buf.Write(owner)
	}
	if group != nil {
		groupOff = uint32(buf.Len())
		buf.Write(group)
	}
	if dacl != nil {
		daclOff = uint32(buf.Len())
		buf.Write(dacl)
	}

	out := buf.Bytes()
	binary.LittleEndian.PutUint32(out[offsetsAt+0:], ownerOff)
	binary.LittleEndian.PutUint32(out[offsetsAt+4:], groupOff)
	binary.LittleEndian.PutUint32(out[offsetsAt+8:], 0) // SACL offset — skipped
	binary.LittleEndian.PutUint32(out[offsetsAt+12:], daclOff)
	return out
}
