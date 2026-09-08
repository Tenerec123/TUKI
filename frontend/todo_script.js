const TaskCreator = document.getElementById('task-creator');
const ProjectCreator = document.getElementById('project-creator');
const RoutineCreator = document.getElementById('routine-creator')
const TaskContainer = document.getElementById('task-container');
const ProjectContainer = document.getElementById('project-container');
const RoutinesContainer = document.getElementById('routine-container');
const CheckCreateButton = document.getElementById('check-create-button');
const TodayRoutines = document.getElementById('today-routine-list')
const SERVER_IP = window.location.hostname;
const API_PORT = window.location.port || "8000"
window.API_URL = `http://${SERVER_IP}:${API_PORT}`;

function getIcon(iconName) {
    if (!iconName) return 'person-walking';
    const el = document.createElement('i');
    el.className = `bi bi-${iconName}`;
    el.style.cssText = 'position:absolute;visibility:hidden';
    document.body.appendChild(el);
    const content = getComputedStyle(el, '::before').getPropertyValue('content');
    document.body.removeChild(el);
    return (content && content !== 'none') ? iconName : 'person-walking';
}

document.addEventListener('DOMContentLoaded', () => {
    // Default date inputs to today instead of a hardcoded (stale) date.
    const today = new Date().toISOString().split('T')[0];
    document.querySelectorAll('.date-container').forEach(container => {
        const text = container.querySelector('.iso-date-text');
        const native = container.querySelector('.iso-date-native');
        if (text) { text.value = today; }
        if (native) { native.value = today; }
    });
    // ISO date inputs: keep the visible text in sync with the native picker value.
    document.querySelectorAll('.iso-date-native').forEach(native => {
        syncIsoDateText(native);
        native.addEventListener('change', () => syncIsoDateText(native));
    });
    LoadTasks();
    LoadProjects();
    LoadRoutines();
    LoadTodayRoutines();
});

function syncIsoDateText(nativeInput) {
    const text = nativeInput.closest('.date-container').querySelector('.iso-date-text');
    if (text) { text.value = nativeInput.value; }
}

async function LoadTasks() {
    await fetch(`${window.API_URL}/api/tasks/`)
    .then(response => response.json())
    .then(data => {

    TaskContainer.innerHTML = '';
    ToSortTaskContainer = [];
    data.forEach(task => {
        Char = "☑";
        SHOW_FINISHED = false
        if (!task.finished){
            Char = "☐";
        }

        if (!task.finished || SHOW_FINISHED){
            const divTask = document.createElement('tr');

            const checkTd = document.createElement('td');
            checkTd.className = 'check';
            const checkBtn = document.createElement('button');
            checkBtn.textContent = Char;
            checkBtn.addEventListener('click', () => {
                CheckClick(checkBtn, task.id);
            });
            checkTd.appendChild(checkBtn);

            const nameTd = document.createElement('td');
            nameTd.className = 'name';
            nameTd.textContent = task.name;

            const descTd = document.createElement('td');
            descTd.className = 'description';
            descTd.textContent = task.description;

            const priorityTd = document.createElement('td');
            priorityTd.className = 'priority';
            priorityTd.textContent = task.priority;

            const deadlineTd = document.createElement('td');
            deadlineTd.className = 'deadline';
            deadlineTd.textContent = task.deadline || 'No date';

            const deleteTd = document.createElement('td');
            deleteTd.className = 'delete';
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = '🗑️';
            deleteBtn.addEventListener('click', () => {
                Delete('tasks', deleteBtn, task.id);
            });
            deleteTd.appendChild(deleteBtn);

            divTask.appendChild(checkTd);
            divTask.appendChild(nameTd);
            divTask.appendChild(descTd);
            divTask.appendChild(priorityTd);
            divTask.appendChild(deadlineTd);
            divTask.appendChild(deleteTd);

            TaskContainer.appendChild(divTask);
        }
        });
    })
    .catch(error => console.error("Error al obtener datos:", error));
}

async function LoadProjects() {
    await fetch(`${window.API_URL}/api/projects/`)
    .then(response => response.json())
    .then(data => {
    ProjectContainer.innerHTML = '';
    data.forEach(project => {
        const divProject = document.createElement('tr');

        const nameTd = document.createElement('td');
        nameTd.className = 'name';
        nameTd.textContent = project.name;

        const descTd = document.createElement('td');
        descTd.className = 'description';
        descTd.textContent = project.description;

        const priorityTd = document.createElement('td');
        priorityTd.className = 'priority';
        priorityTd.textContent = project.priority;

        const deleteTd = document.createElement('td');
        deleteTd.className = 'delete';
        const deleteBtn = document.createElement('button');
        deleteBtn.textContent = '🗑️';
        deleteBtn.addEventListener('click', () => {
            Delete('projects', deleteBtn, project.id);
        });
        deleteTd.appendChild(deleteBtn);

        divProject.appendChild(nameTd);
        divProject.appendChild(descTd);
        divProject.appendChild(priorityTd);
        divProject.appendChild(deleteTd);

        ProjectContainer.appendChild(divProject);
    });
    })
    .catch(error => console.error("Error al obtener datos:", error));
}
async function LoadRoutines() {
  await fetch(`${window.API_URL}/api/routines/`)
  .then(response => response.json())
  .then(data => {
    
    RoutinesContainer.innerHTML = '';
    data.forEach(routine => {
      const divRoutine = document.createElement('tr');
      Char = "☐";
      if (routine.finished){
        Char = "☑";
      }

      const iconTd = document.createElement('td');
      iconTd.className = 'icon';
      iconTd.innerHTML = `<i class="bi bi-${getIcon(routine.icon)}"></i>`;

      const nameTd = document.createElement('td');
      nameTd.className = 'name';
      nameTd.textContent = routine.name;

      const descTd = document.createElement('td');
      descTd.className = 'description';
      descTd.textContent = routine.description;

      const priorityTd = document.createElement('td');
      priorityTd.className = 'priority';
      priorityTd.textContent = routine.priority;

      const frequencyTd = document.createElement('td');
      frequencyTd.className = 'description';
      frequencyTd.textContent = routine.frequency;

      const initDateTd = document.createElement('td');
      initDateTd.className = 'deadline';
      initDateTd.textContent = routine.init_date;

      const deleteTd = document.createElement('td');
      deleteTd.className = 'delete';
      const deleteBtn = document.createElement('button');
      deleteBtn.textContent = '🗑️';
      deleteBtn.addEventListener('click', () => {
        Delete('routines', deleteBtn, routine.id);
      });
      deleteTd.appendChild(deleteBtn);

      divRoutine.appendChild(iconTd);
      divRoutine.appendChild(nameTd);
      divRoutine.appendChild(descTd);
      divRoutine.appendChild(priorityTd);
      divRoutine.appendChild(frequencyTd);
      divRoutine.appendChild(initDateTd);
      divRoutine.appendChild(deleteTd);

      RoutinesContainer.appendChild(divRoutine);
    });
  })
  .catch(error => console.error("Error al obtener datos:", error));
}
async function CheckClick(element, id){
  Checked_str = "false"
  if (element.textContent == "☑"){
    element.innerText  = "☐";
  }else{
    if (id != -1) {element.parentElement.parentElement.remove()}
    else{
        element.innerText  = "☑";
    }
    Checked_str = "true"
  }
  if (id == -1){return}
  const response = await fetch(`${window.API_URL}/api/tasks/${id}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: `{"finished":${Checked_str}}` // Convertimos el objeto a texto JSON
    });
}
async function LoadTodayRoutines() {
   const response = await fetch(`${window.API_URL}/api/routines/today`)
   .then(response => response.json())
   .then(data => {
    data.forEach(routine=>{
        const newRoutine = document.createElement('div');
        newRoutine.classList.add('routine-item');
        if (routine.checked){newRoutine.classList.add('checked')}
        newRoutine.addEventListener('click', async (e) => {
            if (newRoutine.classList.contains('checked')){
                await fetch(`${window.API_URL}/api/routines/uncheck/${routine.id}`, {method:'DELETE'})
            }
            else{
                await fetch(`${window.API_URL}/api/routines/check/${routine.id}`, {method:'POST'})
            }
            newRoutine.classList.toggle('checked')
        });
        newRoutine.setAttribute('data-name', routine.name);
        newRoutine.innerHTML = `<i class="bi bi-${getIcon(routine.icon)}"></i>`;
        TodayRoutines.appendChild(newRoutine);
    })  
   });
}
TaskCreator.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(TaskCreator);
    const data = Object.fromEntries(formData.entries());
    if (CheckCreateButton.textContent == "☐"){
      data.finished = "False"
    }
    else{
      data.finished = "True"
    }
    console.log(data)
    const response = await fetch(`${window.API_URL}/api/tasks/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: JSON.stringify(data) // Convertimos el objeto a texto JSON
    });

    if (response.ok) {
        console.log("TUKI: Tarea guardada con éxito");
        TaskCreator.reset(); // Limpia el formulario
        CheckCreateButton.textContent = "☐"
        LoadTasks();
    }
   
});
ProjectCreator.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(ProjectCreator);
    const data = Object.fromEntries(formData.entries());
    console.log(data)
    const response = await fetch(`${window.API_URL}/api/projects/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: JSON.stringify(data) // Convertimos el objeto a texto JSON
    });

    if (response.ok) {
        console.log("TUKI: Proyecto guardada con éxito");
        ProjectCreator.reset(); // Limpia el formulario
        LoadProjects();
        LoadTasks();
        LoadRoutines();
    }
   
});
RoutineCreator.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(RoutineCreator);
    const data = Object.fromEntries(formData.entries());
    const response = await fetch(`${window.API_URL}/api/routines/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: JSON.stringify(data) // Convertimos el objeto a texto JSON
    });

    if (response.ok) {
        console.log("TUKI: Rutina guardada con éxito");
        RoutineCreator.reset(); // Limpia el formulario
        LoadRoutines();
    }
   
});
async function Delete(type, object, id){
  object.parentElement.parentElement.remove()
  const resp = await fetch(`${window.API_URL}/api/${type}/${id}`,{
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
    });
}

// Este script va a "gritar" el error en la pantalla del móvil
window.onerror = function(msg, url, linenumber) {
    alert('Error: ' + msg + '\nURL: ' + url + '\nLine: ' + linenumber);
    return true;
};
