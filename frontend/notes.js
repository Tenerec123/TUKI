// ── Editor ──
const editor = new EasyMDE({
    element: document.getElementById('mdEditor'),
    status: false,
    toolbar: [
        'bold', 'italic', 'heading', '|',
        'quote', 'unordered-list', 'ordered-list', '|',
        'link', 'image', 'table', 'horizontal-rule', '|',
        'side-by-side', 'fullscreen', '|',
        'guide'
    ],
    spellChecker: false,
    placeholder: 'Start writing...',
    renderingConfig: { codeSyntaxHighlighting: true },
});

const toggle = document.getElementById('previewToggle');
const previewPane = document.getElementById('previewPane');
const editorEl = document.querySelector('.EasyMDEContainer');
let previewMode = false;

function togglePreview() {
    previewMode = !previewMode;
    if (previewMode) {
        previewPane.innerHTML = marked.parse(editor.value() || '_Empty note_');
        previewPane.style.display = 'block';
        editorEl.style.display = 'none';
        toggle.innerHTML = '<i class="bi bi-pencil"></i> EDIT';
        toggle.classList.add('active');
    } else {
        previewPane.style.display = 'none';
        editorEl.style.display = 'flex';
        toggle.innerHTML = '<i class="bi bi-eye"></i> PREVIEW';
        toggle.classList.remove('active');
        editor.codemirror.refresh();
    }
}

toggle.addEventListener('click', togglePreview);

// ── Active note state ──
let activeNoteId = null;
let currentPermission = true; // true = vault, false = draft

function loadNote(id, name) {
    if (activeNoteId === id) return;
    activeNoteId = id;
    document.getElementById('noteTitle').value = name;

    fetch(`/api/notes/${id}`)
        .then(r => r.json())
        .then(data => {
            editor.value(data.content || "");
            if (previewMode) togglePreview();
        })
        .catch(err => console.error("Failed to load note:", err));
}

function saveNote() {
    if (!activeNoteId) return;
    const title = document.getElementById('noteTitle').value;
    const content = editor.value();

    fetch(`/api/notes/${activeNoteId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, content })
    })
    .then(r => {
        if (!r.ok) throw new Error(r.statusText);
        console.log("Note saved");
    })
    .catch(err => console.error("Failed to save note:", err));
}

document.getElementById('saveBtn').addEventListener('click', saveNote);

document.getElementById('syncBtn').addEventListener('click', () => {
    apiSyncChanges().then(() => {
        console.log("AI changes synced to vault");
        fetchTree();
    }).catch(err => console.error("Failed to sync:", err));
});

document.getElementById('discardBtn').addEventListener('click', () => {
    apiDiscardChanges().then(() => {
        console.log("AI changes discarded");
        fetchTree();
    }).catch(err => console.error("Failed to discard:", err));
});

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'p') {
        e.preventDefault();
        togglePreview();
    }
    if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        saveNote();
    }
});

// ── Context menu ──
const ctxMenu = document.getElementById('ctxMenu');

function hideCtxMenu() {
    ctxMenu.classList.remove('open');
    ctxMenu.innerHTML = '';
}

function showCtxMenu(x, y, items, themeClass) {
    ctxMenu.innerHTML = '';
    ctxMenu.className = 'ctx-menu' + (themeClass ? ` ${themeClass}` : '');

    for (const item of items) {
        if (item.type === 'separator') {
            const sep = document.createElement('div');
            sep.className = 'ctx-menu-sep';
            ctxMenu.appendChild(sep);
            continue;
        }

        if (item.type === 'input') {
            const wrap = document.createElement('div');
            wrap.className = 'ctx-menu-input';
            const input = document.createElement('input');
            input.type = 'text';
            input.placeholder = item.placeholder || '';
            wrap.appendChild(input);
            ctxMenu.appendChild(wrap);

            requestAnimationFrame(() => {
                input.focus();
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.stopPropagation();
                        const val = input.value.trim();
                        if (val) { hideCtxMenu(); item.onsubmit(val); }
                    }
                    if (e.key === 'Escape') { e.stopPropagation(); hideCtxMenu(); }
                });
                input.addEventListener('click', (e) => e.stopPropagation());
            });
            continue;
        }

        const el = document.createElement('div');
        el.className = 'ctx-menu-item' + (item.danger ? ' danger' : '');
        el.innerHTML = `<i class="bi ${item.icon}"></i> ${item.label}`;
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            hideCtxMenu();
            item.action();
        });
        ctxMenu.appendChild(el);
    }

    // Position: keep inside viewport
    ctxMenu.style.left = '0';
    ctxMenu.style.top = '0';
    ctxMenu.classList.add('open');

    const rect = ctxMenu.getBoundingClientRect();
    ctxMenu.style.left = (x + rect.width > window.innerWidth ? x - rect.width : x) + 'px';
    ctxMenu.style.top = (y + rect.height > window.innerHeight ? y - rect.height : y) + 'px';
}

// Close on any click outside
document.addEventListener('click', hideCtxMenu);
document.addEventListener('contextmenu', (e) => {
    if (!ctxMenu.contains(e.target)) hideCtxMenu();
});

// ── API helpers ──
function apiCreateNote(title, path) {
    return fetch("/api/notes/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, path: path || "", content: "" })
    }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
}

function apiDeleteNote(id) {
    return fetch(`/api/notes/${id}`, { method: "DELETE" })
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
}

function apiCreateFolder(path) {
    return fetch("/api/notes/folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path })
    }).then(r => { if (!r.ok) throw new Error(r.statusText); });
}

function apiDeleteFolder(path) {
    return fetch("/api/notes/folder", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path })
    }).then(r => { if (!r.ok) throw new Error(r.statusText); });
}

function apiSyncChanges() {
    return fetch("/api/notes/ai/sync", { method: "POST" })
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
}

function apiDiscardChanges() {
    return fetch("/api/notes/ai/discard", { method: "POST" })
        .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
}

// ── File tree ──
const expandedIds = new Set();
let selectedId = null;
let selectedFolderPath = null; // null = root

function fileIcon(name) {
    if (name.endsWith("-api") || name.endsWith("-setup") || name.endsWith("-tuki")) return "bi-file-earmark-code";
    if (name.endsWith("-stt")) return "bi-file-earmark-music";
    return "bi-file-earmark-text";
}

function renderTree(node, container, parentPath = "") {
    for (const file of node.files) {
        const el = document.createElement("div");
        el.className = "file-item" + (file.id === selectedId ? " selected" : "");
        el.dataset.id = file.id;
        el.dataset.name = file.name;
        el.innerHTML = `<i class="bi ${fileIcon(file.name)} file-icon"></i> ${file.name}<button class="delete-badge" title="Delete"><i class="bi bi-x"></i></button>`;
        container.appendChild(el);
    }

    for (const folder of node.folders) {
        const fullPath = parentPath ? `${parentPath}/${folder.name}` : folder.name;
        const isOpen = expandedIds.has(fullPath);

        const folderEl = document.createElement("div");
        folderEl.className = "folder-item" + (isOpen ? " open" : "");
        folderEl.dataset.id = `folder:${folder.name}`;
        folderEl.dataset.path = fullPath;
        folderEl.innerHTML = `<i class="bi bi-chevron-right folder-chevron"></i><i class="bi bi-folder-fill folder-icon"></i> ${folder.name}<button class="delete-badge" title="Delete folder"><i class="bi bi-x"></i></button>`;
        container.appendChild(folderEl);

        const childrenEl = document.createElement("div");
        childrenEl.className = "folder-children" + (isOpen ? " open" : "");
        container.appendChild(childrenEl);

        renderTree(folder, childrenEl, fullPath);
    }
}

function toggleFolder(path) {
    const folderEl = document.querySelector(`.folder-item[data-path="${path}"]`);
    const childrenEl = folderEl?.nextElementSibling;
    if (!folderEl || !childrenEl) return;

    if (expandedIds.has(path)) {
        expandedIds.delete(path);
        folderEl.classList.remove("open");
        childrenEl.classList.remove("open");
    } else {
        expandedIds.add(path);
        folderEl.classList.add("open");
        childrenEl.classList.add("open");
    }
}

function selectItem(id) {
    if (selectedId === id) return;
    document.querySelector(".file-item.selected, .folder-item.selected")?.classList.remove("selected");
    selectedId = id;
    const el = document.querySelector(`[data-id="${id}"]`);
    el?.classList.add("selected");
}

function selectFolder(path) {
    document.querySelector(".folder-item.selected")?.classList.remove("selected");
    selectedFolderPath = path;
    selectedId = null;
    const el = document.querySelector(`.folder-item[data-path="${path}"]`);
    el?.classList.add("selected");
}

function deselectAll() {
    selectedFolderPath = null;
    selectedId = null;
    document.querySelector(".file-item.selected, .folder-item.selected")?.classList.remove("selected");
}

function refreshTree(tree) {
    const container = document.getElementById("fileTree");
    container.innerHTML = "";
    renderTree(tree, container);
}

function findFileByName(node, name) {
    for (const file of node.files) {
        if (file.name === name) return file;
    }
    for (const folder of node.folders) {
        const found = findFileByName(folder, name);
        if (found) return found;
    }
    return null;
}

function fetchTree() {
    return fetch(`/api/notes/tree?permission=${currentPermission}`)
        .then(r => r.json())
        .then(tree => { refreshTree(tree); return tree; })
        .catch(err => console.error("Failed to load file tree:", err));
}

// ── Click handler ──
document.getElementById("fileTree").addEventListener("click", (e) => {
    // Delete badge — highest priority
    const badge = e.target.closest(".delete-badge");
    if (badge) {
        e.stopPropagation();
        const file = badge.closest(".file-item");
        const folder = badge.closest(".folder-item");
        if (file) {
            const id = Number(file.dataset.id);
            apiDeleteNote(id).then(() => {
                if (activeNoteId === id) { activeNoteId = null; editor.value(""); document.getElementById('noteTitle').value = ""; }
                if (selectedId === id) deselectAll();
                fetchTree();
            });
        } else if (folder) {
            const path = folder.dataset.path;
            apiDeleteFolder(path).then(() => {
                expandedIds.delete(path);
                if (selectedFolderPath === path) deselectAll();
                fetchTree();
            });
        }
        return;
    }

    const chevron = e.target.closest(".folder-chevron");
    if (chevron) {
        e.stopPropagation();
        const folder = chevron.closest(".folder-item");
        if (folder) {
            toggleFolder(folder.dataset.path);
            selectFolder(folder.dataset.path);
        }
        return;
    }

    const folder = e.target.closest(".folder-item");
    if (folder) {
        toggleFolder(folder.dataset.path);
        selectFolder(folder.dataset.path);
        return;
    }

    const file = e.target.closest(".file-item");
    if (file) {
        const id = Number(file.dataset.id);
        deselectAll();
        selectedId = id;
        selectItem(id);
        loadNote(id, file.dataset.name);
    } else {
        deselectAll();
    }
});

// ── Context menu: right-click on tree items ──
document.getElementById("fileTree").addEventListener("contextmenu", (e) => {
    const folder = e.target.closest(".folder-item");
    const file = e.target.closest(".file-item");

    if (folder) {
        e.preventDefault();
        const path = folder.dataset.path;

        showCtxMenu(e.clientX, e.clientY, [
            { icon: "bi-file-earmark-plus", label: "New note", action: () => {
                showCtxMenu(e.clientX, e.clientY, [
                    { type: "input", placeholder: "Note title...", onsubmit: (title) => {
                        apiCreateNote(title, path).then(note => {
                            expandedIds.add(path);
                            fetchTree();
                            loadNote(note.id, note.title);
                        });
                    }},
                ]);
            }},
            { type: "separator" },
            { icon: "bi-trash", label: "Delete folder", danger: true, action: () => {
                apiDeleteFolder(path).then(() => {
                    expandedIds.delete(path);
                    if (selectedFolderPath === path) deselectAll();
                    fetchTree();
                });
            }},
        ]);
        return;
    }

    if (file) {
        e.preventDefault();
        const id = Number(file.dataset.id);
        const name = file.dataset.name;

        showCtxMenu(e.clientX, e.clientY, [
            { icon: "bi-box-arrow-up-right", label: "Open", action: () => { selectItem(id); loadNote(id, name); } },
            { type: "separator" },
            { icon: "bi-trash", label: "Delete note", danger: true, action: () => {
                apiDeleteNote(id).then(() => {
                    if (activeNoteId === id) { activeNoteId = null; editor.value(""); document.getElementById('noteTitle').value = ""; }
                    if (selectedId === id) deselectAll();
                    fetchTree();
                });
            }},
        ], 'file-ctx');
        return;
    }

    // Right-click on empty space in tree
    e.preventDefault();
    showCtxMenu(e.clientX, e.clientY, [
        { icon: "bi-file-earmark-plus", label: "New note", action: () => {
            showCtxMenu(e.clientX, e.clientY, [
                { type: "input", placeholder: "Note title...", onsubmit: (title) => {
                    apiCreateNote(title, selectedFolderPath).then(note => {
                        fetchTree();
                        loadNote(note.id, note.title);
                    });
                }},
            ]);
        }},
        { icon: "bi-folder-plus", label: "New folder", action: () => {
            showCtxMenu(e.clientX, e.clientY, [
                { type: "input", placeholder: "Folder name...", onsubmit: (name) => {
                    apiCreateFolder(name).then(() => fetchTree());
                }},
            ]);
        }},
    ]);
});

// ── Sidebar header buttons (same menus) ──
document.getElementById("newFolderBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    showCtxMenu(rect.left, rect.bottom + 4, [
        { type: "input", placeholder: "Folder name...", onsubmit: (name) => {
            apiCreateFolder(name).then(() => fetchTree());
        }},
    ]);
});

document.getElementById("newFileBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    showCtxMenu(rect.left, rect.bottom + 4, [
        { type: "input", placeholder: "Note title...", onsubmit: (title) => {
            apiCreateNote(title, selectedFolderPath).then(note => {
                console.log(note)
                fetchTree();
                loadNote(note.id, note.title);
            });
        }},
    ]);
});

// ── Vault / Draft toggle ──
const vaultToggle = document.getElementById("vaultToggle");

function updateToggleUI() {
    vaultToggle.innerHTML = currentPermission
        ? '<i class="bi bi-database"></i> VAULT'
        : '<i class="bi bi-pencil-square"></i> DRAFT';
    vaultToggle.classList.toggle("draft", !currentPermission);
}

vaultToggle.addEventListener("click", () => {
    const currentName = activeNoteId ? document.getElementById("noteTitle").value : null;
    currentPermission = !currentPermission;
    updateToggleUI();
    deselectAll();
    activeNoteId = null;

    fetchTree().then(tree => {
        if (!tree || !currentName) return;
        const match = findFileByName(tree, currentName);
        if (match && match.id != null) {
            selectItem(match.id);
            selectedId = match.id;
            loadNote(match.id, match.name);
        }
    });
});

// ── Init ──
fetchTree();
