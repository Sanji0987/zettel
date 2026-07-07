import { useEffect, useState, useCallback, useRef } from "react";
import { uniqueFilenames, noteToMarkdown, parseMarkdown, makeZip } from "./vault";

const API = "/api";

const EMPTY = { title: "", text: "", label: "", references: "" };

// File System Access API: full "open a folder" flow. Firefox/Safari lack it, so we
// fall back to a multi-file <input> for import and a .zip download for export.
const SUPPORTS_FS = typeof window !== "undefined" && "showDirectoryPicker" in window;

// Best-effort UI-state persistence; never let a failed save break the editor flow.
function apiSetting(key, value) {
  fetch(`${API}/settings/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value: String(value) }),
  }).catch(() => {});
}

export default function App() {
  const [notes, setNotes] = useState([]);
  const [selectedId, setSelectedId] = useState(null); // null = nothing, "new" = draft
  const [draft, setDraft] = useState(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [vaultMsg, setVaultMsg] = useState(null);

  // ---- Chat state ----
  const [chatLog, setChatLog] = useState([]); // [{role:"user"|"assistant", content, sources?}]
  const [chatInput, setChatInput] = useState("");
  const [chatMode, setChatMode] = useState("read"); // explicit user toggle: read | write
  const [chatSending, setChatSending] = useState(false);
  const [ollamaOk, setOllamaOk] = useState(null); // null = unknown, then bool
  const [cogneeOk, setCogneeOk] = useState(null);
  const chatLogRef = useRef(null);

  const dirHandleRef = useRef(null); // set once a directory is picked
  const fileInputRef = useRef(null); // fallback <input type=file>
  const textRef = useRef(null); // editor textarea (for cursor restore)

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

  // Restore the last open note (persisted in SQLite settings) once notes load.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || loading) return;
    restoredRef.current = true;
    (async () => {
      try {
        const res = await fetch(`${API}/settings/last_open_note_id`);
        const { value } = await res.json();
        const id = parseInt(value, 10);
        const n = notes.find((x) => x.id === id);
        if (n) openNote(n);
      } catch {
        /* ignore — nothing to restore */
      }
    })();
  }, [loading, notes]);

  function newNote() {
    setSelectedId("new");
    setDraft(EMPTY);
  }

  function openNote(n) {
    setSelectedId(n.id);
    setDraft({ title: n.title, text: n.text, label: n.label, references: n.references });
    apiSetting("last_open_note_id", n.id); // persist across reloads (SQLite)
  }

  // Cursor position is ephemeral / per-browser -> localStorage, not SQLite.
  function saveCursor() {
    if (selectedId === null || selectedId === "new" || !textRef.current) return;
    localStorage.setItem(`cursor:${selectedId}`, String(textRef.current.selectionStart));
  }
  useEffect(() => {
    if (selectedId === null || selectedId === "new" || !textRef.current) return;
    const pos = parseInt(localStorage.getItem(`cursor:${selectedId}`) || "", 10);
    if (!Number.isNaN(pos)) {
      textRef.current.focus();
      textRef.current.setSelectionRange(pos, pos);
    }
  }, [selectedId]);

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
      apiSetting("last_open_note_id", saved.id);
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
      const files = uniqueFilenames(all); // [{ note, name }] — collision-free filenames
      if (dirHandleRef.current && SUPPORTS_FS) {
        const opts = { mode: "readwrite" };
        const h = dirHandleRef.current;
        if (h.queryPermission && (await h.queryPermission(opts)) !== "granted") {
          if ((await h.requestPermission(opts)) !== "granted")
            throw new Error("Folder write permission denied");
        }
        for (const { note, name } of files) {
          const fh = await h.getFileHandle(name, { create: true });
          const w = await fh.createWritable();
          await w.write(noteToMarkdown(note));
          await w.close();
        }
        setVaultMsg(`Exported ${all.length} note(s) to the folder.`);
      } else {
        const enc = new TextEncoder();
        const blob = makeZip(
          files.map(({ note, name }) => ({ name, data: enc.encode(noteToMarkdown(note)) }))
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

  // ---- Chat ---------------------------------------------------------------

  // On load, probe backend integrations so the UI can enable/disable chat.
  useEffect(() => {
    (async () => {
      try {
        const s = await (await fetch(`${API}/ollama/status`)).json();
        setOllamaOk(!!s.reachable);
      } catch {
        setOllamaOk(false);
      }
      try {
        const s = await (await fetch(`${API}/cognee/status`)).json();
        setCogneeOk(!!s.configured);
      } catch {
        setCogneeOk(false);
      }
    })();
  }, []);

  // Keep the chat log scrolled to the newest message.
  useEffect(() => {
    if (chatLogRef.current) chatLogRef.current.scrollTop = chatLogRef.current.scrollHeight;
  }, [chatLog, chatSending]);

  // Single poster for both normal turns and decision answers. `body` is the /api/chat
  // payload; `userBubble` (optional) is echoed into the log first.
  async function postChat(body, userBubble) {
    if (userBubble) setChatLog((log) => [...log, userBubble]);
    setChatSending(true);
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`Chat failed (${res.status})`);
      const { reply, sources, pending_decision } = await res.json();
      setChatLog((log) => [
        ...log,
        { role: "assistant", content: reply, sources: sources || [], pending_decision: pending_decision || null },
      ]);
    } catch (e) {
      setChatLog((log) => [...log, { role: "assistant", content: `⚠ ${e.message}`, sources: [] }]);
    } finally {
      setChatSending(false);
    }
  }

  function sendChat() {
    const message = chatInput.trim();
    if (!message || chatSending) return;
    const history = chatLog.map(({ role, content }) => ({ role, content }));
    setChatInput("");
    postChat({ message, mode: chatMode, history }, { role: "user", content: message });
  }

  // Generic: answer any pending_decision. The brain routes on {type, choice}; the
  // original question is recovered from history (its last user turn).
  function answerDecision(pd, option) {
    if (chatSending) return;
    const history = chatLog.map(({ role, content }) => ({ role, content }));
    setChatLog((log) => log.map((m) => (m.pending_decision ? { ...m, pending_decision: null } : m)));
    postChat(
      { message: "", mode: "read", history, decision_response: { id: pd.id, type: pd.type, choice: option.id } },
      { role: "user", content: option.label },
    );
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
              {n.pending_ingest && <span className="tag pending">pending</span>}
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
              ref={textRef}
              className="text-input"
              placeholder="Write your atomic note…"
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
              onSelect={saveCursor}
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

      <section className="chat">
        <div className="chat-head">
          <div className="chat-head-top">
            <h2>Chat</h2>
            <span className="status-dot" title="Ollama container on n8n-net">
              <span className={`dot ${ollamaOk === null ? "" : ollamaOk ? "on" : "off"}`} />
              Ollama
            </span>
          </div>
          <div className="segmented" role="tablist" aria-label="Chat mode">
            <button
              className={chatMode === "read" ? "active" : ""}
              onClick={() => setChatMode("read")}
            >
              Read
            </button>
            <button
              className={chatMode === "write" ? "active" : ""}
              onClick={() => setChatMode("write")}
            >
              Write
            </button>
          </div>
        </div>

        <div className="chat-log" ref={chatLogRef}>
          {chatLog.length === 0 && (
            <p className="muted">Ask about your notes ({chatMode} mode).</p>
          )}
          {chatLog.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.content}
              {m.sources && m.sources.length > 0 && (
                <div className="sources">
                  Sources:{" "}
                  {m.sources.map((s, j) => (
                    <span key={j}>
                      {j > 0 && ", "}
                      {typeof s === "string" ? s : s.url ? <a href={s.url}>{s.title || s.url}</a> : JSON.stringify(s)}
                    </span>
                  ))}
                </div>
              )}
              {/* Generic mid-conversation decision: prompt + a plain button per option. */}
              {m.pending_decision && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ marginBottom: 4, fontSize: "0.9em" }}>{m.pending_decision.prompt}</div>
                  {(m.pending_decision.options || []).map((opt) => (
                    <button
                      key={opt.id}
                      disabled={chatSending}
                      style={{ marginRight: 6 }}
                      onClick={() => answerDecision(m.pending_decision, opt)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          {chatSending && <div className="bubble assistant muted">…</div>}
        </div>

        {ollamaOk === false && (
          <p className="chat-hint error">
            Ollama unreachable. Run <code>docker start ollama</code> and reload.
          </p>
        )}
        <div className="chat-input">
          <input
            placeholder={ollamaOk === false ? "Chat disabled — Ollama offline" : "Message…"}
            value={chatInput}
            disabled={ollamaOk === false || chatSending}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendChat()}
          />
          <button
            className="btn-primary"
            onClick={sendChat}
            disabled={ollamaOk === false || chatSending || !chatInput.trim()}
          >
            Send
          </button>
        </div>
      </section>
    </div>
  );
}
