import { useEffect, useState, useCallback } from "react";

const API = "/api";

const EMPTY = { title: "", text: "", label: "", references: "" };

export default function App() {
  const [notes, setNotes] = useState([]);
  const [selectedId, setSelectedId] = useState(null); // null = nothing, "new" = draft
  const [draft, setDraft] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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

  const editing = selectedId !== null;

  return (
    <div className="app">
      <aside className="sidebar">
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
