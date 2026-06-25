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
let mediaStream = null;
let chunks = [];

function SetupStream(stream) {
    mediaStream = stream;
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
    recorder.start();
    can_record = true;
    mic.innerHTML = `<i class="bi bi-mic-fill"></i>`;
}

async function ToggleMic() {
    // ── Stop recording ──
    if (is_recording) {
        if (recorder && recorder.state === 'recording') {
            recorder.stop();
        }
        if (mediaStream) {
            mediaStream.getTracks().forEach(t => t.stop());
        }
        mediaStream = null;
        recorder = null;
        can_record = false;
        is_recording = false;
        mic.innerHTML = `<i class="bi bi-mic"></i>`;
        return;
    }

    // ── Start recording ──
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        console.warn("Micrófono no soportado en este navegador");
        return;
    }

    try {
        is_recording = true;
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        console.log("Acceso al micro concedido");
        SetupStream(stream);
    } catch (err) {
        console.error("Error al obtener stream:", err);
        is_recording = false;
        // En iOS, getUserMedia requiere HTTPS
        if (/iPhone|iPad|iPod/i.test(navigator.userAgent) && location.protocol !== 'https:') {
            alert("El micrófono requiere HTTPS en iOS. Usá la PC o conectate vía HTTPS.");
        }
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
                if (message.is_user){
                    msg_div.classList.add('user-msg')
                    msg_div.innerHTML = message.text;
                }
                else{
                    msg_div.classList.add('tuki-msg');
                    msg_div.innerHTML = marked.parse(message.text);
                }
                chatContainer.appendChild(msg_div)
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
    
    const response = await fetch(`${window.API_URL}/api/ai/connect/${conv_id}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    });
    if (!response.ok){return}
    console.log("TUKI_MSG_CREATED")
    const tukiMsg = document.createElement('div');
    tukiMsg.classList.add('tuki-msg');
    chatContainer.appendChild(tukiMsg);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";

    while (true) {
        const { done, value } = await reader.read();
        
        if (value) {
            // Decodificar y acumular
            const chunk = decoder.decode(value, { stream: true });
            fullText += chunk;
            
            // Renderizado progresivo
            tukiMsg.innerHTML = marked.parse(fullText);
            Render();
        }
        if (done) {
            decoder.decode(); // Limpiar buffer del decoder
            const scroll_dif = chatContainer.clientHeight - tukiMsg.scrollHeight;
            console.log(scroll_dif);
            if (scroll_dif > 0) {
                chatContainer.style.paddingBottom = `${scroll_dif-100}px`;
            } else {
                chatContainer.style.paddingBottom = "100px";
            }
            break;
        }
    }
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
    userMsg.innerHTML = text;
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
    const response = await fetch(`${window.API_URL}/api/ai/connect/${idOfSelectedConv}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json'
        }
    });

    const tukiMsg = document.createElement('div');
    tukiMsg.classList.add('tuki-msg');
    chatContainer.appendChild(tukiMsg);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";

    while (true) {
        const { done, value } = await reader.read();
        
        if (value) {
            // Decodificar y acumular
            const chunk = decoder.decode(value, { stream: true });
            fullText += chunk;
            
            // Renderizado progresivo
            tukiMsg.innerHTML = marked.parse(fullText);
            Render();
        }
        if (done) {
            decoder.decode(); // Limpiar buffer del decoder
            const scroll_dif = chatContainer.clientHeight - tukiMsg.scrollHeight;
            console.log(scroll_dif);
            if (scroll_dif > 0) {
                chatContainer.style.paddingBottom = `${scroll_dif-100}px`;
            } else {
                chatContainer.style.paddingBottom = "100px";
            }
            break;
        }
    }
}

function scrollToBottom(){
    setTimeout(() => {
        chatContainer.scrollTo({
            top: chatContainer.scrollHeight,
            behavior: 'smooth' // Movimiento fluido, no salto brusco
        });
    }, 10); // 10ms es suficiente para que el DOM se actualice
}

function Render(){
    renderMathInElement(document.body, {
      delimiters: [
          {left: '$$', right: '$$', display: true},  // Ecuaciones centradas
          {left: '$', right: '$', display: false},  // Ecuaciones inline
          {left: '\\(', right: '\\)', display: true},
          {left: '\\[', right: '\\]', display: false}
      ],
      throwOnError : true
    });
}

document.addEventListener('DOMContentLoaded', async () => {

    const container = document.getElementById('chat-sidebar-container');
    
    toggleBtn.addEventListener('click', () => {
        container.classList.toggle('sidebar-collapsed');
    });
    await getConversations();
    if (conversationList.children){
        conversationList.children[0].children[0].click();
    }
    Render();
    SetupAudio();

    const agentSelectors = document.querySelectorAll('.sub-details');
    agentSelectors.forEach(agentS => {

        possibleModels = agentS.querySelectorAll('.model-item');

        possibleModels.forEach(model => {
            model.addEventListener('click', () => {
                const selectedBefore = agentS.querySelector('.selected-model');
                if (selectedBefore == model) {return}
                selectedBefore.classList.remove('selected-model')
                model.classList.add('selected-model');
                agentS.querySelector('.model-current-name').textContent = model.textContent;
                agentS.open = false;

            })
        })
    })
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

function SendModelConfig(){
    function getSelectedModel(spanId) {
        const sub = document.getElementById(spanId).closest('.sub-details');
        const sel = sub.querySelector('.selected-model');
        return sel ? sel.getAttribute('data-model') : 'deepseek/deepseek-v4-flash';
    }
    fetch(`${window.API_URL}/api/config/models`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            "get_data": getSelectedModel('get-data-name'),
            "exec_tools": getSelectedModel('exec-tools-name'),
            "final_resp": getSelectedModel('final-resp-name'),
            "general": getSelectedModel('general-name')
        })
    });
}

document.addEventListener('click', (e) => {
    if (menu_displayed != null && !e.target.closest('.conv-options') && !menu_displayed.contains(e.target)){
        menu_displayed.remove();
        menu_displayed = null;
        id_of_menu_disp = null;
    }
    const modelSelector = document.querySelector('.model-selector-details');
    if (!e.target.closest('.model-selector-details') && modelSelector && modelSelector.open){
        SendModelConfig();
        modelSelector.open = false;
    }
})