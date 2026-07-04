// Vault <-> note mapping and a dependency-free .zip writer.
//
// The vault folder is a sync CHECKPOINT, not a live second source of truth:
// import reads files INTO SQLite, export writes SQLite OUT to files.
//
// Mapping: filename (minus .md) = title, file body = text. label/references, when
// present, are written as simple YAML-ish frontmatter and parsed back on import.

export function filenameFor(note) {
  const base =
    (note.title || "untitled").replace(/[\/\\:*?"<>|]/g, "_").trim() || "untitled";
  return `${base}.md`;
}

// Assign a unique .md filename to every note in a set. Two notes with the same title
// (e.g. two "Test" notes) would otherwise both map to Test.md and overwrite each other
// on export — so collisions get a -2, -3, ... suffix. Returns [{ note, name }].
export function uniqueFilenames(notes) {
  const used = new Set();
  return notes.map((note) => {
    const base = filenameFor(note).replace(/\.md$/i, "");
    let name = `${base}.md`;
    let i = 2;
    while (used.has(name.toLowerCase())) {
      name = `${base}-${i}.md`;
      i++;
    }
    used.add(name.toLowerCase());
    return { note, name };
  });
}

export function noteToMarkdown(note) {
  const label = (note.label || "").trim();
  const refs = (note.references || "").trim();
  const lines = [];
  if (label || refs) {
    lines.push("---");
    if (label) lines.push(`label: ${label}`);
    // references may be newline-separated; keep the key on one line via "; ".
    if (refs) lines.push(`references: ${refs.split(/\r?\n/).filter(Boolean).join("; ")}`);
    lines.push("---", "");
  }
  lines.push(note.text || "");
  return lines.join("\n");
}

export function parseMarkdown(filename, content) {
  const title = filename.replace(/\.md$/i, "");
  let label = "";
  let references = "";
  let body = content;
  const fm = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (fm) {
    body = content.slice(fm[0].length);
    for (const line of fm[1].split(/\r?\n/)) {
      const m = line.match(/^(\w+):\s*(.*)$/);
      if (!m) continue;
      if (m[1] === "label") label = m[2].trim();
      else if (m[1] === "references")
        references = m[2].split(/\s*;\s*/).filter(Boolean).join("\n");
    }
  }
  return { title, text: body.replace(/^\n+/, ""), label, references };
}

// ---- minimal store-method (uncompressed) zip -----------------------------

function crc32(bytes) {
  let crc = -1;
  for (let i = 0; i < bytes.length; i++) {
    let c = (crc ^ bytes[i]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ -1) >>> 0;
}

const u16 = (n) => new Uint8Array([n & 255, (n >>> 8) & 255]);
const u32 = (n) =>
  new Uint8Array([n & 255, (n >>> 8) & 255, (n >>> 16) & 255, (n >>> 24) & 255]);

function concat(arrs) {
  let len = 0;
  for (const a of arrs) len += a.length;
  const out = new Uint8Array(len);
  let p = 0;
  for (const a of arrs) {
    out.set(a, p);
    p += a.length;
  }
  return out;
}

// files: [{ name: string, data: Uint8Array }] -> Blob (application/zip)
export function makeZip(files) {
  const enc = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    const local = concat([
      u32(0x04034b50), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(f.data.length), u32(f.data.length),
      u16(name.length), u16(0), name, f.data,
    ]);
    chunks.push(local);
    central.push(concat([
      u32(0x02014b50), u16(20), u16(20), u16(0), u16(0), u16(0), u16(0),
      u32(crc), u32(f.data.length), u32(f.data.length),
      u16(name.length), u16(0), u16(0), u16(0), u16(0), u32(0),
      u32(offset), name,
    ]));
    offset += local.length;
  }

  const cdStart = offset;
  let cdSize = 0;
  for (const c of central) {
    chunks.push(c);
    cdSize += c.length;
  }
  chunks.push(concat([
    u32(0x06054b50), u16(0), u16(0),
    u16(files.length), u16(files.length),
    u32(cdSize), u32(cdStart), u16(0),
  ]));

  return new Blob(chunks, { type: "application/zip" });
}
