const NT_MASK_LABELS: Record<string, string> = {
  READ_DATA:        "Read",
  LIST_DIRECTORY:   "List directory",
  WRITE_DATA:       "Write",
  ADD_FILE:         "Add file",
  APPEND_DATA:      "Append",
  ADD_SUBDIRECTORY: "Add subdirectory",
  READ_EA:          "Read extended attrs",
  WRITE_EA:         "Write extended attrs",
  EXECUTE:          "Execute",
  TRAVERSE:         "Traverse",
  DELETE_CHILD:     "Delete child",
  READ_ATTRIBUTES:  "Read attributes",
  WRITE_ATTRIBUTES: "Write attributes",
  DELETE:           "Delete",
  READ_CONTROL:     "Read permissions",
  WRITE_DAC:        "Change permissions",
  WRITE_OWNER:      "Take ownership",
  SYNCHRONIZE:      "Synchronize",
  GENERIC_READ:     "Generic read",
  GENERIC_WRITE:    "Generic write",
  GENERIC_EXECUTE:  "Generic execute",
  GENERIC_ALL:      "Full Control",
};

const NFSV4_MASK_LABELS: Record<string, string> = {
  read_data:        "Read",
  list_directory:   "List directory",
  write_data:       "Write",
  add_file:         "Add file",
  append_data:      "Append",
  add_subdirectory: "Add subdirectory",
  read_named_attrs: "Read named attrs",
  write_named_attrs:"Write named attrs",
  execute:          "Execute",
  delete_child:     "Delete child",
  read_attributes:  "Read attributes",
  write_attributes: "Write attributes",
  delete:           "Delete",
  read_acl:         "Read ACL",
  write_acl:        "Change permissions",
  write_owner:      "Take ownership",
  synchronize:      "Synchronize",
};

const FLAG_LABELS: Record<string, string> = {
  file_inherit:       "File inherit",
  dir_inherit:        "Directory inherit",
  inherit_only:       "Inherit only",
  no_propagate:       "No propagate",
  inherited:          "Inherited",
  object_inherit:     "Object inherit",
  container_inherit:  "Container inherit",
  successful_access:  "Audit success",
  failed_access:      "Audit failure",
  identifier_group:   "Group",
};

const CONTROL_LABELS: Record<string, string> = {
  dacl_present:    "DACL present",
  dacl_protected:  "DACL protected (no inherit)",
  dacl_auto_inherited: "DACL auto-inherited",
  sacl_present:    "SACL present",
  self_relative:   "Self relative",
};

function pretty(table: Record<string, string>, key: string): string {
  return table[key] ?? key.toUpperCase();
}

export function formatNtMask(bit: string): string {
  return pretty(NT_MASK_LABELS, bit);
}

export function formatNfsV4Mask(bit: string): string {
  return pretty(NFSV4_MASK_LABELS, bit);
}

export function formatAceFlag(flag: string): string {
  return pretty(FLAG_LABELS, flag);
}

export function formatNtControl(flag: string): string {
  return pretty(CONTROL_LABELS, flag);
}
