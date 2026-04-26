/**
 * Format a Unix mode integer as `drwxr-xr-x`-style string.
 * Mode is the full mode incl. file-type bits (top 4 bits) and the 12 perm bits.
 */
export function formatMode(mode: number | null | undefined): string {
  if (mode == null) return "—";
  const fileType = (mode & 0o170000) >>> 0;
  let kind = "-";
  if (fileType === 0o040000) kind = "d";
  else if (fileType === 0o120000) kind = "l";
  else if (fileType === 0o060000) kind = "b";
  else if (fileType === 0o020000) kind = "c";
  else if (fileType === 0o010000) kind = "p";
  else if (fileType === 0o140000) kind = "s";

  const perm = (group: number, special: number, specialChar: string) => {
    const r = (group & 4) ? "r" : "-";
    const w = (group & 2) ? "w" : "-";
    let x = (group & 1) ? "x" : "-";
    if (special) {
      x = (group & 1) ? specialChar.toLowerCase() : specialChar.toUpperCase();
    }
    return r + w + x;
  };

  const setuid = (mode & 0o4000) ? 1 : 0;
  const setgid = (mode & 0o2000) ? 1 : 0;
  const sticky = (mode & 0o1000) ? 1 : 0;

  return (
    kind +
    perm((mode >>> 6) & 0o7, setuid, "s") +
    perm((mode >>> 3) & 0o7, setgid, "s") +
    perm(mode & 0o7, sticky, "t")
  );
}

/** Format mode as octal: 0755 / 4755 etc. */
export function formatOctal(mode: number | null | undefined): string {
  if (mode == null) return "—";
  return (mode & 0o7777).toString(8).padStart(4, "0");
}

/**
 * Pick a small icon path describing a file/folder. Returns an SVG `d` attribute.
 * Folder vs document, with a tiny visual hint for known extensions.
 */
export function iconPathForKind(kind: string, extension?: string | null): string {
  if (kind === "directory") {
    return "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z";
  }
  switch ((extension || "").toLowerCase()) {
    case "jpg":
    case "jpeg":
    case "png":
    case "gif":
    case "webp":
    case "svg":
      // image
      return "M4 4h16v16H4zM4 16l4-4 4 4 4-4 4 4M9 9a1 1 0 100-2 1 1 0 000 2z";
    case "pdf":
    case "doc":
    case "docx":
      return "M6 2a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6H6zM14 2v6h6";
    case "zip":
    case "tar":
    case "gz":
    case "7z":
      return "M21 8v13H3V8M1 3h22v5H1zM10 12h4";
    default:
      return "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6";
  }
}
