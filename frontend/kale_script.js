const SERVER_IP = window.location.hostname;
const API_PORT = window.location.port || "8000"
window.API_URL = `http://${SERVER_IP}:${API_PORT}`;
document.addEventListener('DOMContentLoaded', async () => {
    tasks = []
    await fetch(`${window.API_URL}/api/tasks`)
    .then(response => response.json())
    .then(data => {
        data.forEach(task => {
        const [dia, mes, año] = task.deadline.split('/');
        const ISO_date = `${año}-${mes}-${dia}`;
        tasks.push({
            title:task.name,
            start:ISO_date,
            allDay: true,
            className: task.finished ? 'cal-task-done' : ISO_date >= new Date().toISOString().split('T')[0] ? 'cal-task-pending' : 'cal-task-overdue'
        })
    });
    })
    .catch(error => console.error("Error al obtener datos:", error));
    var calendarEl = document.getElementById('calendar');
    var calendar = new FullCalendar.Calendar(calendarEl, {
        initialView: 'dayGridMonth',
        firstDay:1,
        locale: 'en',
        headerToolbar: {
        left: 'prev,next today',
        center: 'title',
        right: 'dayGridMonth,timeGridWeek,timeGridDay'
      },
      events: tasks
    });
    calendar.render();
  });

