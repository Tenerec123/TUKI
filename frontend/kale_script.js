document.addEventListener('DOMContentLoaded', async () => {
    tasks = []
    await fetch('http://localhost:8000/api/tasks')
    .then(response => response.json())
    .then(data => {
    console.log(data)
    data.forEach(task => {
        const [dia, mes, año] = task.deadline.split('/');
        console.log(dia)
        const ISO_date = `${año}-${mes}-${dia}`;
        tasks.push({
            title:task.name,
            start:ISO_date,
            allDay: true,
            className: task.finished ? 'cal-task-done' : 'cal-task-pending',
        })
    });
    })
    .catch(error => console.error("Error al obtener datos:", error));
    console.log(tasks)
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

