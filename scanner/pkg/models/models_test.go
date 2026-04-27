package models

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestACL_MarshalJSON_UnknownTypeErrors(t *testing.T) {
	a := &ACL{Type: "windows7"}
	_, err := json.Marshal(a)
	if err == nil {
		t.Fatal("expected error for unknown ACL type")
	}
	if !strings.Contains(err.Error(), "windows7") {
		t.Errorf("error should mention type: %v", err)
	}
}

func TestACL_MarshalJSON_NilEmitsNull(t *testing.T) {
	var a *ACL
	out, err := json.Marshal(a)
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != "null" {
		t.Errorf("got %q want null", out)
	}
}
