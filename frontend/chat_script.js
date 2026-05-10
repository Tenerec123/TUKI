const chatForm = document.getElementById('prompt-form');
const chatContainer = document.getElementById('chat-container');
const createChatBTN = document.getElementById('create-chat');
const conversationList = document.getElementById('conversation-list');
const textarea = document.getElementById('prompt-writer')
const SERVER_IP = window.location.hostname;
const toggleBtn = document.getElementById('toggle-sidebar');
const API_PORT = window.location.port || "8000"
window.API_URL = `http://${SERVER_IP}:${API_PORT}`;
var posOfSelectedConv = -1;
var idOfSelectedConv = -1;
var menu_displayed = null;
var id_of_menu_disp = null;

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
    });
    getConversations();
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
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="bi bi-three-dots" viewBox="0 0 16 16">
                        <path d="M3 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3"/>
                    </svg>
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
    sendPrompt();
});
async function sendPrompt(){
    const formData = new FormData(chatForm);
    const data = Object.fromEntries(formData.entries());
    if (!data.text || data.text.trim() === "") {
        return;
    }
    let AllChat = [];
    const msgs = chatContainer.children;

    for (let i = 0; i < msgs.length; i++) {
        AllChat.push(msgs[i].textContent);
    }
    
    userMsg = document.createElement('div')
    userMsg.classList.add('user-msg')
    userMsg.innerHTML = data.text;
    chatContainer.appendChild(userMsg)
    scrollToBottom();

    document.getElementById('prompt-writer').value = "";
    
    const response = await fetch(`${window.API_URL}/api/ai/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: JSON.stringify({
            conversation_id:idOfSelectedConv,
            user_message:data.text
        })// Convertimos el objeto a texto JSON
    });
    const result = await response.json(); 

        // 2. Ahora accedemos a la propiedad "response" que definiste en Python

    tukiMsg = document.createElement('div');
    tukiMsg.classList.add('tuki-msg');
    tukiMsg.innerHTML = marked.parse(result.response);
    chatContainer.appendChild(tukiMsg);
    scrollToBottom();
}
textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendPrompt(); 
    }
});
function scrollToBottom(){
    setTimeout(() => {
        chatContainer.scrollTo({
            top: chatContainer.scrollHeight,
            behavior: 'smooth' // Movimiento fluido, no salto brusco
        });
    }, 10); // 10ms es suficiente para que el DOM se actualice
}
document.addEventListener('DOMContentLoaded', async () => {
    const container = document.getElementById('chat-sidebar-container');
    
    toggleBtn.addEventListener('click', () => {
        container.classList.toggle('sidebar-collapsed');
    });
    await getConversations();
    conversationList.children[0].children[0].click()
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
        <button class="menu-item conv-rename" onClick="allowRenameConv(${id}, ${position})">Cambiar nombre</button>
        <button class="menu-item" onClick="deleteConversation(${id})">Eliminar</button>
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
document.addEventListener('click', (e) => {
    if (menu_displayed != null && !e.target.closest('.conv-options') && !menu_displayed.contains(e.target)){
        menu_displayed.remove();
        menu_displayed = null;
        id_of_menu_disp = null;
    }
})