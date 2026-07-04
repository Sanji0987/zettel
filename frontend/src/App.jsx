import { useEffect, useState, useCallback, useRef } from "react";
import { filenameFor, noteToMarkdown, parseMarkdown, makeZip } from "./vault";

const API = "/api";

const EMPTY = { title: "", text: "", label: "", references: "" };

// File System Access API: full "open a folder" flow. Firefox/Safari lack it, so we
// fall back to a multi-file <input> for import and a .zip download for export.
const SUPPORTS_FS = typeof window !== "undefined" && "showDirectoryPicker" in window;

export default function App() {
  const [notes, setNotes] = useState([]);
  const [selectedId, setSelectedId] = useState(null); // null = nothing, "new" = draft
  const [draft, setDraft] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [vaultMsg, setVaultMsg] = useState(null);

  const dirHandleRef = useRef(null); // set once a directory is picked
  const fileInputRef = useRef(null); // fallback <input type=file>

  const loadNotes = useCallback(async () => {
    try {
      const res = await fetch(`${API}/notes`);
      if (!res.ok) throw new Error(`List failed (${res.status})`);
      setNotes(await res.json());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadNotes();
  }, [loadNotes]);

  function newNote() {
    setSelectedId("new");
    setDraft(EMPTY);
  }

  function openNote(n) {
    setSelectedId(n.id);
    setDraft({ title: n.title, text: n.text, label: n.label, references: n.references });
  }

  async function save() {
    try {
      const isNew = selectedId === "new";
      const res = await fetch(
        isNew ? `${API}/notes` : `${API}/notes/${selectedId}`,
        {
          method: isNew ? "POST" : "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        }
      );
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      const saved = await res.json();
      await loadNotes();
      setSelectedId(saved.id);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(id) {
    if (!confirm("Delete this note?")) return;
    try {
      const res = await fetch(`${API}/notes/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`Delete failed (${res.status})`);
      if (selectedId === id) {
        setSelectedId(null);
        setDraft(EMPTY);
      }
      await loadNotes();
    } catch (e) {
      setError(e.message);
    }
  }

  // ---- Vault import/export ------------------------------------------------

  async function importParsed(objs) {
    if (objs.length === 0) {
      setVaultMsg("No .md files found.");
      return;
    }
    const res = await fetch(`${API}/notes/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(objs),
    });
    if (!res.ok) throw new Error(`Import failed (${res.status})`);
    const { imported } = await res.json();
    await loadNotes();
    setVaultMsg(`Imported ${imported} of ${objs.length} note(s).`);
  }

  async function openVault() {
    setVaultMsg(null);
    setError(null);
    if (SUPPORTS_FS) {
      let handle;
      try {
        handle = await window.showDirectoryPicker();
      } catch (e) {
        if (e.name !== "AbortError") setError(e.message);
        return; // user cancelled
      }
      try {
        dirHandleRef.current = handle;
        const objs = [];
        for await (const entry of handle.values()) {
          if (entry.kind === "file" && entry.name.toLowerCase().endsWith(".md")) {
            const content = await (await entry.getFile()).text();
            objs.push(parseMarkdown(entry.name, content));
          }
        }
        await importParsed(objs);
      } catch (e) {
        setError(e.message);
      }
    } else {
      fileInputRef.current?.click(); // fallback: multi-file picker
    }
  }

  async function onFilesPicked(e) {
    const files = Array.from(e.target.files || []).filter((f) =>
      f.name.toLowerCase().endsWith(".md")
    );
    try {
      const objs = await Promise.all(
        files.map(async (f) => parseMarkdown(f.name, await f.text()))
      );
      await importParsed(objs);
    } catch (err) {
      setError(err.message);
    } finally {
      e.target.value = ""; // allow re-picking the same files
    }
  }

  async function exportVault() {
    setVaultMsg(null);
    setError(null);
    try {
      const all = await (await fetch(`${API}/notes/export`)).json();
      if (dirHandleRef.current && SUPPORTS_FS) {
        const opts = { mode: "readwrite" };
        const h = dirHandleRef.current;
        if (h.queryPermission && (await h.queryPermission(opts)) !== "granted") {
          if ((await h.requestPermission(opts)) !== "granted")
            throw new Error("Folder write permission denied");
        }
        for (const note of all) {
          const fh = await h.getFileHandle(filenameFor(note), { create: true });
          const w = await fh.createWritable();
          await w.write(noteToMarkdown(note));
          await w.close();
        }
        setVaultMsg(`Exported ${all.length} note(s) to the folder.`);
      } else {
        const enc = new TextEncoder();
        const blob = makeZip(
          all.map((note) => ({ name: filenameFor(note), data: enc.encode(noteToMarkdown(note)) }))
        );
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "vault.zip";
        a.click();
        URL.revokeObjectURL(url);
        setVaultMsg(`Downloaded vault.zip (${all.length} note(s)).`);
      }
    } catch (e) {
      setError(e.message);
    }
  }

  const editing = selectedId !== null;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="vault-bar">
          <button className="btn-ghost" onClick={openVault}>Open Vault</button>
          <button className="btn-ghost" onClick={exportVault}>Export Vault</button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".md"
          multiple
          hidden
          onChange={onFilesPicked}
        />
        {vaultMsg && <p className="muted">{vaultMsg}</p>}

        <div className="sidebar-head">
          <h1>Zettelkeistan</h1>
          <button className="btn-primary" onClick={newNote}>+ New</button>
        </div>
        {loading && <p className="muted">Loading…</p>}
        {error && <p className="error">Backend: {error}</p>}
        <ul className="note-list">
          {notes.map((n) => (
            <li
              key={n.id}
              className={n.id === selectedId ? "active" : ""}
              onClick={() => openNote(n)}
            >
              <div className="note-title">{n.title || "(untitled)"}</div>
              <div className="note-preview">{n.text.slice(0, 60)}</div>
              {n.label && <span className="tag">{n.label}</span>}
            </li>
          ))}
          {!loading && notes.length === 0 && <p className="muted">No notes yet.</p>}
        </ul>
      </aside>

      <main className="editor">
        {!editing ? (
          <div className="empty-state">
            <p>Select a note or create a new one.</p>
          </div>
        ) : (
          <>
            <input
              className="title-input"
              placeholder="Title"
              value={draft.title}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
            <textarea
              className="text-input"
              placeholder="Write your atomic note…"
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            />
            <div className="meta-row">
              <input
                placeholder="Label"
                value={draft.label}
                onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              />
              <input
                placeholder="References (URLs, newline-separated)"
                value={draft.references}
                onChange={(e) => setDraft({ ...draft, references: e.target.value })}
              />
            </div>
            <div className="actions">
              <button className="btn-primary" onClick={save}>Save</button>
              {selectedId !== "new" && (
                <button className="btn-danger" onClick={() => remove(selectedId)}>
                  Delete
                </button>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
