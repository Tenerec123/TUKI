const TaskCreator = document.getElementById('task-creator');
const RoutineCreator = document.getElementById('routine-creator')
const TaskContainer = document.getElementById('task-container');
const RoutinesContainer = document.getElementById('routine-container');
const RoutineTrackers = document.getElementById('routine-trackers');
const RoutineTrackersDetails = document.getElementById('routine-trackers-details');
const CheckCreateButton = document.getElementById('check-create-button');
const TodayRoutines = document.getElementById('today-routine-list')
let CurrentRoutineTrackers = [];

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

const PRIORITY_META = {
    1: { label: 'Critical', cls: 'priority-critical' },
    2: { label: 'High', cls: 'priority-high' },
    3: { label: 'Normal', cls: 'priority-normal' },
    4: { label: 'Low', cls: 'priority-low' },
};

// Render a task or routine priority as an English label with a color
// class. Out-of-range or missing values fall back to Normal.
function setPriority(td, priority) {
    const meta = PRIORITY_META[priority] || PRIORITY_META[3];
    td.textContent = meta.label;
    td.classList.add(meta.cls);
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
    LoadRoutines();
    LoadTodayRoutines();
});

function syncIsoDateText(nativeInput) {
    const text = nativeInput.closest('.date-container').querySelector('.iso-date-text');
    if (text) { text.value = nativeInput.value; }
}

async function LoadTasks() {
    await fetch(`/api/tasks/`)
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
            setPriority(priorityTd, task.priority);

            const deadlineTd = document.createElement('td');
            deadlineTd.className = 'deadline';
            deadlineTd.textContent = task.deadline || 'No date';

            const deleteTd = document.createElement('td');
            deleteTd.className = 'delete';
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = '🗑️';
            deleteBtn.addEventListener('click', () => {
                Delete('tasks', task.id);
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

function LoadRoutineTrackers(routines, loadDetails = RoutineTrackersDetails.open) {
    CurrentRoutineTrackers = routines;
    RoutineTrackers.replaceChildren();
    if (routines.length === 0) {
        const emptyState = document.createElement('p');
        emptyState.className = 'routine-trackers-empty';
        emptyState.textContent = 'No routines yet.';
        RoutineTrackers.appendChild(emptyState);
        return;
    }

    routines.forEach(routine => {
        const tracker = document.createElement('article');
        tracker.className = 'routine-tracker';
        tracker.dataset.routineId = routine.id;

        const identity = document.createElement('div');
        identity.className = 'routine-tracker-identity';

        const icon = document.createElement('i');
        icon.className = `bi bi-${getIcon(routine.icon)}`;
        icon.setAttribute('aria-hidden', 'true');

        const name = document.createElement('span');
        name.className = 'routine-tracker-name';
        name.textContent = routine.name;

        const heatmap = document.createElement('div');
        heatmap.className = 'routine-tracker-heatmap';
        heatmap.id = `routine-heatmap-${routine.id}`;
        heatmap.setAttribute('role', 'img');
        heatmap.setAttribute('aria-label', `${routine.name} tracker for the last 12 months`);

        identity.append(icon, name);
        tracker.append(identity, heatmap);
        RoutineTrackers.appendChild(tracker);
    });

    if (loadDetails && routines.length > 0) {
        LoadRoutineHeatmaps(routines);
    }
}

function LoadRoutineHeatmaps(routines) {
    routines.forEach(routine => {
        const heatmap = document.getElementById(`routine-heatmap-${routine.id}`);
        if (heatmap) LoadRoutineHeatmap(routine.id, heatmap);
    });
}

async function LoadRoutineHeatmap(id, heatmapElement) {
    try {
        heatmapElement.replaceChildren();
        const response = await fetch(`/api/routines/stats/${id}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const checks = await response.json();
        const checkData = {};

        checks.forEach(check => {
            const [year, month, day] = check.check_date.split('-').map(Number);
            const timestamp = Math.floor(new Date(year, month - 1, day).getTime() / 1000);
            checkData[timestamp] = 1;
        });

        const cellPadding = 2;
        const domainGutter = 0;
        const availableWidth = heatmapElement.getBoundingClientRect().width || window.innerWidth;
        const cellSize = Math.max(1, Math.min(12, Math.floor(availableWidth / 54) - cellPadding - domainGutter));
        const initDate = moment().subtract(1, 'years').startOf('week').toDate();
        const lowLimit = moment().subtract(1, 'years').startOf('day');
        const upLimit = moment().startOf('day');

        new CalHeatMap().init({
            itemSelector: `#${heatmapElement.id}`,
            domain: 'week',
            subDomain: 'day',
            rowLimit: 7,
            range: 54,
            cellSize,
            cellPadding,
            domainGutter,
            start: initDate,
            data: checkData,
            displayLegend: false,
            legend: [1],
            legendColors: ['#1f1f1f', '#ffff00'],
            onComplete: function() {
                d3.select(heatmapElement).selectAll('svg.graph-subdomain-group g')
                    .filter(function(d) {
                        return d && d.t && (d.t <= lowLimit || d.t > upLimit);
                    })
                    .remove();
            },
            tooltip: true,
            subDomainTitleFormat: {
                empty: '{date}',
                filled: '{date}'
            },
            subDomainDateFormat: date => moment(date).format('LL'),
            domainLabelFormat: date => moment(date).date() <= 7 ? moment(date).format('MMM') : ''
        });
    } catch (error) {
        console.error(`Error loading tracker for routine ${id}:`, error);
        heatmapElement.textContent = 'Tracker unavailable';
        heatmapElement.classList.add('routine-tracker-error');
    }
}

RoutineTrackersDetails.addEventListener('toggle', () => {
    if (RoutineTrackersDetails.open) {
        LoadRoutineHeatmaps(CurrentRoutineTrackers);
    }
});

async function LoadRoutines() {
  const detailsAreOpen = RoutineTrackersDetails.open;
  await fetch(`/api/routines/`)
  .then(response => response.json())
  .then(data => {
    LoadRoutineTrackers(data, detailsAreOpen);
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
      setPriority(priorityTd, routine.priority);

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
        Delete('routines', routine.id);
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
  if (id === -1) {
    element.textContent = element.textContent === '☑' ? '☐' : '☑';
    return;
  }

  const finished = element.textContent !== '☑';
  try {
    const response = await fetch(`/api/tasks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ finished })
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    console.error(`Error updating task ${id}:`, error);
  }
  await LoadTasks();
}
async function LoadTodayRoutines() {
   try {
    const response = await fetch(`/api/routines/today`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    TodayRoutines.replaceChildren();
    data.forEach(routine=>{
        const newRoutine = document.createElement('div');
        newRoutine.classList.add('routine-item');
        if (routine.checked){newRoutine.classList.add('checked')}
        newRoutine.addEventListener('click', async (e) => {
            const wasChecked = newRoutine.classList.contains('checked');
            const url = wasChecked
                ? `/api/routines/uncheck/${routine.id}`
                : `/api/routines/check/${routine.id}`;
            try {
                const updateResponse = await fetch(url, { method: wasChecked ? 'DELETE' : 'POST' });
                if (!updateResponse.ok) throw new Error(`HTTP ${updateResponse.status}`);
            } catch (error) {
                console.error(`Error updating routine ${routine.id}:`, error);
            }
            await RefreshRoutineUI();
        });
        newRoutine.setAttribute('data-name', routine.name);
        newRoutine.innerHTML = `<i class="bi bi-${getIcon(routine.icon)}"></i>`;
        TodayRoutines.appendChild(newRoutine);
    })  
   } catch (error) {
    console.error('Error loading today routines:', error);
   }
}

async function RefreshRoutineUI() {
    await Promise.all([LoadRoutines(), LoadTodayRoutines()]);
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
    const response = await fetch(`/api/tasks/`, {
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
        await LoadTasks();
    }
   
});
RoutineCreator.addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData(RoutineCreator);
    const data = Object.fromEntries(formData.entries());
    const response = await fetch(`/api/routines/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json' // Le decimos a la API: "Va un JSON"
        },
        body: JSON.stringify(data) // Convertimos el objeto a texto JSON
    });

    if (response.ok) {
        console.log("TUKI: Rutina guardada con éxito");
        RoutineCreator.reset(); // Limpia el formulario
        await RefreshRoutineUI();
    }
   
});
async function Delete(type, id){
  try {
    const response = await fetch(`/api/${type}/${id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    console.error(`Error deleting ${type.slice(0, -1)} ${id}:`, error);
  }

  if (type === 'tasks') await LoadTasks();
  if (type === 'routines') await RefreshRoutineUI();
}

// Este script va a "gritar" el error en la pantalla del móvil
window.onerror = function(msg, url, linenumber) {
    alert('Error: ' + msg + '\nURL: ' + url + '\nLine: ' + linenumber);
    return true;
};
