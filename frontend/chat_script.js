const chatForm = document.getElementById('prompt-form');
const chatContainer = document.getElementById('chat-container');
const createChatBTN = document.getElementById('create-chat');
const conversationList = document.getElementById('conversation-list');
const textarea = document.getElementById('prompt-writer')
const SERVER_IP = window.location.hostname;
const toggleBtn = document.getElementById('toggle-sidebar');
const API_PORT = window.location.port || "8000";
const mic = document.getElementById('toggle-mic');
window.API_URL = `http://${SERVER_IP}:${API_PORT}`;
let posOfSelectedConv = -1;
let idOfSelectedConv = -1;
let menu_displayed = null;
let id_of_menu_disp = null;

let can_record = false;
let is_recording = false;
let recorder = null;
let chunks = [];

function SetupStream(stream) {
    recorder = new MediaRecorder(stream);

    recorder.ondataavailable = e => {
        chunks.push(e.data);
    };

    recorder.onstop = async () => {
        const blob = new Blob(chunks, { type: "audio/ogg; codecs=opus" });
        chunks = [];
        const formData = new FormData();
        formData.append('file', blob, 'recording.ogg');
        formData.append('conv_id', idOfSelectedConv)
        const response = await fetch(`${window.API_URL}/api/ai/stt`, {
            method: 'POST',
            body: formData,
        }).then(response => response.json())
        .then(data => {
            sendPrompt(data)
        });
    };
    can_record = true;
}
async function ToggleMic() {
    if (is_recording) {
        // ── Stop ──
        if (recorder) {
            recorder.stop();
        }
        is_recording = false;
        can_record = false;
        recorder = null;
        chunks = [];
        mic.innerHTML = `<i class="bi bi-mic"></i>`;
        return;
    }

    // ── Start ──
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.warn("Micrófono no disponible: navigator.mediaDevices.getUserMedia no existe.");
        console.warn("En Android, navigator.mediaDevices solo está disponible en contextos seguros (HTTPS o localhost).");
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        console.log("Acceso al micro concedido");
        SetupStream(stream);
        is_recording = true;
        recorder.start();
        mic.innerHTML = `<i class="bi bi-mic-fill"></i>`;
    } catch (err) {
        console.error("Error al obtener stream:", err);
    }
}
mic.addEventListener('click', ToggleMic);

async function createConversation(){
    chat = {
        title:"new_chat"
    }
    const response = await fetch(`${window.API_URL}/api/conversations/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body:JSON.stringify(chat)
    }).then(response => response.json()).then(async data =>{
        await getConversations();
        idOfSelectedConv = -1
        posOfSelectedConv = -1
        loadConversation(data.id, 0);
    });

}
async function deleteConversation(conv_id){
    const response = await fetch(`${window.API_URL}/api/conversations/${conv_id}`, {method: 'DELETE'});
    if (conv_id == idOfSelectedConv){
        chatContainer.innerHTML = ""
        idOfSelectedConv = -1
        posOfSelectedConv = -1
    }
    menu_displayed.remove();
    menu_displayed = null;
    getConversations();
}

async function allowRenameConv(conv_id, conv_position) {
    renameForm = document.createElement('form');
    titleButton = conversationList.children[conv_position].children[0];
    renameForm.classList.add('rename-form');
    renameForm.innerHTML = `<input name="newname" type="text" class="conv-select active-rename" placeholder="${titleButton.textContent}">`;
    titleButton.replaceWith(renameForm);
    renameForm.children[0].focus();
    function detectClicksForRename(e){
        conv_rename = e.target.closest('.conv-rename')
        if ((!conv_rename || conv_rename == renameForm.parentNode.children[1])  && !e.target.closest('.active-rename')){
            console.log("EIEIEI")
            selectBtn = document.createElement('button');
            selectBtn.classList.add('conv-select');
            selectBtn.addEventListener('click', (e) => {
                loadConversation(conv_id, conv_position);
            });
            selectBtn.innerHTML = `${renameForm.children[0].placeholder}`
            renameForm.children[0].blur();
            renameForm.replaceWith(selectBtn);
            document.removeEventListener('click', detectClicksForRename)
        }
}
    document.addEventListener('click', detectClicksForRename);

    renameForm.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const formData = new FormData(document.getElementsByClassName('rename-form')[0]);
            const data = Object.fromEntries(formData.entries());
            const newName = data.newname
            const response2 = await fetch(`${window.API_URL}/api/conversations/${conv_id}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    title:newName
                })
            });
            selectBtn = document.createElement('button');
            selectBtn.classList.add('conv-select');
            selectBtn.addEventListener('click', (e) => {
                loadConversation(conv_id, conv_position);
            });
            selectBtn.innerHTML = `${newName}`
            renameForm.children[0].blur();
            renameForm.replaceWith(selectBtn);
            document.removeEventListener('click', detectClicksForRename);
        }
        else if (e.key == 'Escape'){
            e.preventDefault();
            selectBtn = document.createElement('button');
            selectBtn.classList.add('conv-select');
            selectBtn.addEventListener('click', (e) => {
                loadConversation(conv_id, conv_position);
            });
            selectBtn.innerHTML = `${renameForm.children[0].placeholder}`
            renameForm.children[0].blur();
            renameForm.replaceWith(selectBtn);
            document.removeEventListener('click', detectClicksForRename);
        }
    });
    
}
const TOOL_STYLES = {
    WebSearch:        { icon: '🔍', label: 'Web Search',        color: '#4f8ef7', collapsible: true },
    Weather:          { icon: '🌤️', label: 'Weather',          color: '#00bcd4', collapsible: true },
    Stocks:           { icon: '📈', label: 'Stocks',            color: '#2e7d32', collapsible: true },
    CheckEmail:       { icon: '✉️', label: 'Check Email',       color: '#e91e63', collapsible: true },
    GetAllTasks:      { icon: '📋', label: 'Get All Tasks',     color: '#4caf50', collapsible: true },
    SearchTasks:      { icon: '🔎', label: 'Search Tasks',      color: '#4caf50', collapsible: true },
    GetAllProjects:   { icon: '📁', label: 'Get All Projects',  color: '#4caf50', collapsible: true },
    SearchProjects:   { icon: '🔎', label: 'Search Projects',   color: '#4caf50', collapsible: true },
    GetAllRoutines:   { icon: '🔁', label: 'Get All Routines',  color: '#4caf50', collapsible: true },
    SearchRoutines:   { icon: '🔎', label: 'Search Routines',   color: '#4caf50', collapsible: true },
    ReadNote:         { icon: '📝', label: 'Read Note',         color: '#ff9800', collapsible: true },
    DraftReadNote:    { icon: '📝', label: 'Draft Read Note',   color: '#ff9800', collapsible: true },
    GetCurrentTime:   { icon: '🕐', label: 'Current Time',      color: '#9e9e9e', collapsible: false },
    CreateTask:       { icon: '➕', label: 'Create Task',       color: '#4caf50', collapsible: false },
    DeleteTask:       { icon: '🗑️', label: 'Delete Task',       color: '#e53935', collapsible: false },
    UpdateTask:       { icon: '✏️', label: 'Update Task',       color: '#ffb300', collapsible: false },
    CreateRoutine:    { icon: '➕', label: 'Create Routine',    color: '#4caf50', collapsible: false },
    DeleteRoutine:    { icon: '🗑️', label: 'Delete Routine',    color: '#e53935', collapsible: false },
    UpdateRoutine:    { icon: '✏️', label: 'Update Routine',    color: '#ffb300', collapsible: false },
    CreateProject:    { icon: '➕', label: 'Create Project',    color: '#4caf50', collapsible: false },
    DeleteProject:    { icon: '🗑️', label: 'Delete Project',    color: '#e53935', collapsible: false },
    UpdateProject:    { icon: '✏️', label: 'Update Project',    color: '#ffb300', collapsible: false },
    DraftCreateNote:  { icon: '➕', label: 'Create Note',       color: '#4caf50', collapsible: false },
    DraftDeleteNote:  { icon: '🗑️', label: 'Delete Note',       color: '#e53935', collapsible: false },
    DraftUpdateNote:  { icon: '✏️', label: 'Update Note',       color: '#ffb300', collapsible: false },
    default:          { icon: '🔧', label: 'Tool',              color: '#9e9e9e', collapsible: false }
};

function getToolStyle(name) {
    const known = TOOL_STYLES[name];
    if (known) return known;
    return { ...TOOL_STYLES.default, label: name };
}

function formatToolArg(arg) {
    if (arg == null) return "";
    if (typeof arg === 'string') {
        try { arg = JSON.parse(arg); } catch (e) { return arg; }
    }
    return JSON.stringify(arg, null, 2);
}

function formatArgsKV(arg) {
    if (arg == null) return "";
    let obj = arg;
    if (typeof obj === 'string') {
        try { obj = JSON.parse(obj); } catch (e) { return arg; }
    }
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
        return typeof obj === 'string' ? obj : JSON.stringify(obj);
    }
    const lines = [];
    for (const [k, v] of Object.entries(obj)) {
        const val = (v !== null && typeof v === 'object') ? JSON.stringify(v) : String(v);
        lines.push(`${k}: ${val}`);
    }
    return lines.join("\n");
}

function argsNeedToggle(text) {
    if (!text || !text.trim()) return false;
    return text.length > 40 || text.includes("\n");
}

function renderToolBlock(div, tool) {
    const style = getToolStyle(tool.name);
    div.classList.add('tool-block');
    div.style.setProperty('--accent', style.color);
    const header = document.createElement('div');
    header.classList.add('tool-block-header');
    header.innerHTML = `<span class="tool-block-icon">${style.icon}</span><span class="tool-block-name">${style.label}</span>`;
    div.appendChild(header);

    const argsText = formatArgsKV(tool.args);
    if (argsText.trim()) {
        const argsEl = document.createElement('div');
        argsEl.classList.add('tool-block-args');
        if (argsNeedToggle(argsText)) {
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            summary.textContent = "Args";
            const argsPre = document.createElement('pre');
            argsPre.textContent = argsText;
            details.appendChild(summary);
            details.appendChild(argsPre);
            argsEl.appendChild(details);
        } else {
            const argsPre = document.createElement('pre');
            argsPre.textContent = argsText;
            argsEl.appendChild(argsPre);
        }
        div.appendChild(argsEl);
    }

    if (!div.isConnected) {
        chatContainer.appendChild(div);
    }
    return div;
}

function appendToolResult(div, tool) {
    const style = getToolStyle(tool.name);
    const resultEl = document.createElement('div');
    resultEl.classList.add('tool-block-result');
    const content = formatToolArg(tool.result);
    if (style.collapsible && content.length > 150) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = "Result";
        const pre = document.createElement('pre');
        pre.textContent = content;
        details.appendChild(summary);
        details.appendChild(pre);
        resultEl.appendChild(details);
    } else {
        const pre = document.createElement('pre');
        pre.textContent = content;
        resultEl.appendChild(pre);
    }
    div.appendChild(resultEl);
}

function createTukiMsg() {
    const div = document.createElement('div');
    div.classList.add('tuki-msg');
    chatContainer.appendChild(div);
    return div;
}

function finalizeStream(tukiMsg) {
    // Streaming is complete: render the last message's markdown once.
    renderTukiMarkdown(tukiMsg);
    // #form-container is absolutely positioned over the bottom of the chat
    // area. On desktop its 80px input box is vertically centered, so its top
    // edge sits 100px above the container bottom; 130px clears it with a
    // 30px margin. Keeping this constant (instead of deriving it from the
    // last message height) prevents both the hidden-tail bug and huge empty
    // gaps below short answers.
    chatContainer.style.paddingBottom = "130px";
    scrollToBottom();
}

function handleStreamLine(line, state) {
    if (!line.trim()) return;
    let chunkObj;
    try {
        chunkObj = JSON.parse(line);
    } catch (e) {
        return;
    }
    if (chunkObj.type == "agent" && chunkObj.content.trim() != "") {
        if (!state.tukiMsg || state.lastEvent == "tool") {
            state.tukiMsg = createTukiMsg();
        }
        state.tukiMsg.textContent += chunkObj.content;
        state.lastEvent = "agent";
        Render();
    }
    else if (chunkObj.type == "tool_call") {
        // The streamed text phase is complete; render its markdown before the
        // tool block so a later agent phase starts a fresh message.
        if (state.tukiMsg && state.tukiMsg.textContent.trim()) {
            renderTukiMarkdown(state.tukiMsg);
            state.tukiMsg = null;
        }
        const tool = chunkObj.content;
        const div = document.createElement('div');
        renderToolBlock(div, tool);
        state.callBuffer.push({ id: tool.id, div, tool });
        state.lastEvent = "tool";
    }
    else if (chunkObj.type == "tool_result") {
        const entry = state.callBuffer.find(c => c.id == chunkObj.content.id);
        if (entry) {
            entry.tool.result = chunkObj.content.result;
            appendToolResult(entry.div, entry.tool);
            state.lastEvent = "tool";
        }
    }
    scrollToBottom();
}

async function streamConversation(convId) {
    const response = await fetch(`${window.API_URL}/api/ai/connect/${convId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    });
    if (!response.ok) return;

    const state = { tukiMsg: null, lastEvent: "init", callBuffer: [] };
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const { done, value } = await reader.read();
        if (value) {
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();
            for (const line of lines) {
                handleStreamLine(line, state);
            }
        }
        if (done) {
            decoder.decode();
            if (buffer.trim()) handleStreamLine(buffer, state);
            finalizeStream(state.tukiMsg);
            break;
        }
    }
}

async function loadConversation(conv_id, conv_position){
    conversationList.children[conv_position].classList.add('selected-conversation');
    if (conv_position != posOfSelectedConv){
        if (posOfSelectedConv != -1){
        conversationList.children[posOfSelectedConv].classList.remove('selected-conversation');
        }
        chatContainer.innerHTML = ""
        const response = await fetch(`${window.API_URL}/api/conversations/${conv_id}`)
        .then(response => response.json())
        .then(data => {
            data.messages.forEach((message)=>{
                msg_div = document.createElement('div')
                if (message.type === 'prompt'){
                    msg_div.classList.add('user-msg')
                    msg_div.textContent = message.text;
                    chatContainer.appendChild(msg_div)
                }
                else if (message.type == 'agent' && message.text.trim()){
                    msg_div.classList.add('tuki-msg');
                    msg_div.innerHTML = DOMPurify.sanitize(marked.parse(message.text))
                    chatContainer.appendChild(msg_div)
                }
                else if (message.type == 'tool'){
                    try {
                        toolObj = JSON.parse(message.text)
                        renderToolBlock(msg_div, toolObj);
                        if (toolObj.result != null) appendToolResult(msg_div, toolObj);
                    } catch (e) {
                        msg_div.classList.add('tuki-msg');
                        msg_div.textContent = message.text;
                        chatContainer.appendChild(msg_div);
                    }
                }
                else {
                    chatContainer.appendChild(msg_div)
                }
                scrollToBottom();
                
            })
        });        
    }
    posOfSelectedConv = conv_position
    idOfSelectedConv = conv_id
    if (window.innerWidth <= 768){
        toggleBtn.click();
    }
    Render();
    await streamConversation(conv_id);
}
async function getConversations(){
    const response = await fetch(`${window.API_URL}/api/conversations/`)
    .then(response => response.json())
    .then(data => {
        conversationList.innerHTML = ""
        let i = 0;
        data.forEach(conversation => {
            const divConv = document.createElement('li');
            divConv.innerHTML = `
                <button class="conv-select" onclick="loadConversation(${conversation.id}, ${i})">${conversation.title}</button>
                <button class="conv-options" onclick="OpenMenu(this, ${conversation.id}, ${i})">
                    <i class="bi bi-three-dots"></i>
                </button>
            `;
            // Añadimos este nuevo div al contenedor principal
            conversationList.appendChild(divConv);
            i++;
        });
    });
}
chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(chatForm);
    const data = Object.fromEntries(formData.entries());
    if (!data.text || data.text.trim() === "") {
        return;
    }
    sendPrompt(data.text);
});
textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const formData = new FormData(chatForm);
        const data = Object.fromEntries(formData.entries());
        if (!data.text || data.text.trim() === "") {
            return;
        }
        sendPrompt(data.text);
    }
});

async function sendPrompt(text){
    if (idOfSelectedConv == -1){return}
    userMsg = document.createElement('div')
    userMsg.classList.add('user-msg')
    userMsg.textContent = text;
    chatContainer.appendChild(userMsg);
    chatContainer.style.paddingBottom = `${400}px`;
    scrollToBottom();
    document.getElementById('prompt-writer').value = "";
    
    await fetch(`${window.API_URL}/api/ai/execute`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            conversation_id:idOfSelectedConv,
            user_message:text
        })
    });
    console.log("DONE")
    await streamConversation(idOfSelectedConv);
}

function scrollToBottom(){
    setTimeout(() => {
        chatContainer.scrollTo({
            top: chatContainer.scrollHeight,
            behavior: 'smooth' // Movimiento fluido, no salto brusco
        });
    }, 10); // 10ms es suficiente para que el DOM se actualice
}

const KATEX_OPTIONS = {
    delimiters: [
        {left: '$$', right: '$$', display: true},  // Ecuaciones centradas
        {left: '$', right: '$', display: false},  // Ecuaciones inline
        {left: '\\(', right: '\\)', display: true},
        {left: '\\[', right: '\\]', display: false}
    ],
    throwOnError: true
};

// Render a completed streamed message: sanitize markdown, then math scoped to
// the message element (avoids re-scanning the whole document on every chunk).
function renderTukiMarkdown(msg) {
    if (!msg || !msg.textContent.trim()) return;
    try {
        msg.innerHTML = DOMPurify.sanitize(marked.parse(msg.textContent));
        renderMathInElement(msg, KATEX_OPTIONS);
    } catch (e) {
        // Rendering is a nice-to-have: on failure keep the raw text so stream
        // finalization (padding/scroll) is never blocked.
        console.error('[RENDER] Markdown render failed:', e);
    }
}

function Render(){
    renderMathInElement(document.body, KATEX_OPTIONS);
}

document.addEventListener('DOMContentLoaded', async () => {

    const container = document.getElementById('chat-sidebar-container');
    
    toggleBtn.addEventListener('click', () => {
        container.classList.toggle('sidebar-collapsed');
    });

    // Attach model selector handlers FIRST so they work even if async init fails.
    const agentSelectors = document.querySelectorAll('.sub-details');
    agentSelectors.forEach(agentS => {

        const possibleModels = agentS.querySelectorAll('.model-item');

        possibleModels.forEach(model => {
            model.addEventListener('click', () => {
                const selectedBefore = agentS.querySelector('.selected-model');
                if (selectedBefore == model) {return}
                if (selectedBefore) { selectedBefore.classList.remove('selected-model') }
                model.classList.add('selected-model');
                agentS.querySelector('.model-current-name').textContent = model.textContent;
                agentS.open = false;
                SendModelConfig();
            })
        })
    })

    await getConversations();
    if (conversationList.children){
        conversationList.children[0].children[0].click();
    }
    await LoadModelConfig();
    Render();
})

function OpenMenu(button, id, position){
    const rect = button.getBoundingClientRect();
    const x = rect.left; 
    const y = rect.bottom + 5;

    const a = menu_displayed != null;
    if (a){
        menu_displayed.remove();
        menu_displayed = null;
    }
    const b = !a || id_of_menu_disp != id;
    if (b){
        menu = document.createElement('div');
        Object.assign(menu.style, {
            top: `${y}px`,
            left: `${x}px`
        });
        menu.classList.add('context-menu');
        menu.innerHTML = `
        <button class="menu-item conv-rename" onClick="allowRenameConv(${id}, ${position})">Rename</button>
        <button class="menu-item" onClick="deleteConversation(${id})">Delete</button>
        `;
        
        document.body.appendChild(menu);
        menu_displayed = menu;
    }
    if (a && !b){
        id_of_menu_disp = null;
    }
    else{
        id_of_menu_disp = id;
    }
}

async function LoadModelConfig(){
    try {
        const res = await fetch(`${window.API_URL}/api/config/models`);
        if (!res.ok) { return }
        const config = await res.json();
        const targets = [
            ['orchestrator-name', config.orchestrator],
            ['searcher-name', config.searcher],
        ];
        for (const [spanId, model] of targets) {
            if (!model) { continue }
            const sub = document.getElementById(spanId).closest('.sub-details');
            sub.querySelectorAll('.model-item').forEach(item => {
                if (item.getAttribute('data-model') === model) {
                    sub.querySelectorAll('.selected-model').forEach(s => s.classList.remove('selected-model'));
                    item.classList.add('selected-model');
                    sub.querySelector('.model-current-name').textContent = item.textContent;
                }
            });
        }
    } catch (e) {
        console.error('[CONFIG] Failed to load model config:', e);
    }
}

function SendModelConfig(){
    function getSelectedModel(spanId) {
        const sub = document.getElementById(spanId).closest('.sub-details');
        const sel = sub.querySelector('.selected-model');
        return sel ? sel.getAttribute('data-model') : null;
    }
    const config = {
        "orchestrator": getSelectedModel('orchestrator-name'),
        "searcher": getSelectedModel('searcher-name'),
    };
    if (!config.orchestrator || !config.searcher) {
        console.warn('[CONFIG] Missing model selection, skipping save');
        return;
    }
    fetch(`${window.API_URL}/api/config/models`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(config)
    }).catch(e => console.error('[CONFIG] Failed to save model config:', e));
}

document.addEventListener('click', (e) => {
    if (menu_displayed != null && !e.target.closest('.conv-options') && !menu_displayed.contains(e.target)){
        menu_displayed.remove();
        menu_displayed = null;
        id_of_menu_disp = null;
    }
    const modelSelector = document.querySelector('.model-selector-details');
    if (!e.target.closest('.model-selector-details') && modelSelector && modelSelector.open){
        modelSelector.open = false;
    }
})