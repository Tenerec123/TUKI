
const TaskCreator = document.getElementById('task-creator');
const ProjectCreator = document.getElementById('project-creator');
const RoutineCreator = document.getElementById('routine-creator')

const TaskContainer = document.getElementById('task-container');
const ProjectContainer = document.getElementById('project-container');
const RoutinesContainer = document.getElementById('routine-container');


const CheckCreateButton = document.getElementById('check-create-button');
document.addEventListener('DOMContentLoaded', () => {
    LoadTasks();
    LoadProjects();
    LoadRoutines();
});

async function LoadTasks() {
    await fetch('http://localhost:8000/api/tasks')
    .then(response => response.json())
    .then(data => {
    
    // Limpiamos el contenedor por si había algo antes
    TaskContainer.innerHTML = '';
    // 'data' es tu lista de tareas. Usamos un bucle para leer cada una:
    console.log(data)
    data.forEach(task => {
        Char = "☑";
        SHOW_FINISHED = false
        if (!task.finished){
            Char = "☐";
        }

        if (!task.finished || SHOW_FINISHED){
            // Creamos un elemento para la tarea
            const divTask = document.createElement('tr');
        
            divTask.innerHTML = `
                <td class="check"><button onclick="CheckClick(this, ${task.id})">${Char}</button></td>
                <td class="name">${task.name}</td>
                <td class="description">${task.description}</td>
                <td class="priority">${task.priority}</td>
                <td class="deadline">${task.deadline || 'No date'}</td>
                <td class="delete"><button onclick="Delete('tasks', this, ${task.id})">🗑️</button></td>
            `;

            // Añadimos este nuevo div al contenedor principal
            TaskContainer.appendChild(divTask);
        }
        
        });
    })
    .catch(error => console.error("Error al obtener datos:", error));
}

async function LoadProjects() {
    await fetch('http://localhost:8000/api/projects')
    .then(response => response.json())
    .then(data => {
    
    // Limpiamos el contenedor por si había algo antes
    ProjectContainer.innerHTML = '';
    // 'data' es tu lista de tareas. Usamos un bucle para leer cada una:
    data.forEach(project => {
      // Creamos un elemento para la tarea
        const divProject = document.createElement('tr');
        divProject.innerHTML = `
            <td class="name">${project.name}</td>
            <td class="description">${project.description}</td>
            <td class="priority">${project.priority}</td>
            <td class="delete"><button onclick="Delete('projects', this, ${project.id})">🗑️</button></td>
        `;

        // Añadimos este nuevo div al contenedor principal
        ProjectContainer.appendChild(divProject);
    });
    })
    .catch(error => console.error("Error al obtener datos:", error));
}
// Not done
async function LoadRoutines() {
  await fetch('http://localhost:8000/api/routines')
  .then(response => response.json())
  .then(data => {
    
    // Limpiamos el contenedor por si había algo antes
    RoutinesContainer.innerHTML = '';
    // 'data' es tu lista de tareas. Usamos un bucle para leer cada una:
    console.log(data)
    data.forEach(routine => {
      // Creamos un elemento para la tarea
      const divRoutine = document.createElement('tr');
      Char = "☐";
      if (routine.finished){
        Char = "☑";
      }
      divRoutine.innerHTML = `
        <td class="name">${routine.name}</td>
        <td class="description">${routine.description}</td>
        <td class="priority">${routine.priority}</td>
        <td class="description">${routine.frequency}</td>
        <td class="deadline">${routine.last_run || 'No date'}</td>
        <td class="deadline">${routine.nex_run || 'No date'}</td>
        <td class="delete"><button onclick="Delete('routines', this, ${routine.id})">🗑️</button></td>
      `;
      // Añadimos este nuevo div al contenedor principal
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
    element.parentElement.parentElement.remove()
    // element.innerText  = "☑";
    Checked_str = "true"
  }
  const response = await fetch(`http://localhost:8000/api/tasks/${id}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: `{"finished":${Checked_str}}` // Convertimos el objeto a texto JSON
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
    const response = await fetch('http://localhost:8000/api/tasks', {
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
    const response = await fetch('http://localhost:8000/api/projects', {
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
    const response = await fetch('http://localhost:8000/api/routines', {
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
  const resp = await fetch(`http://localhost:8000/api/${type}/${id}`,{
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
    });
}
