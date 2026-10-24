async function LoadCalendar(){
    const calendarPage = document.querySelector('.kale-page');
    const header = document.querySelector('header');
    let calendar;
    const resizeCalendarPage = () => {
        const viewportHeight = window.visualViewport?.height - 50;
        const availableHeight = Math.max(0, viewportHeight - header.getBoundingClientRect().height - 2);
        calendarPage.style.height = `${availableHeight}px`;
        calendar?.updateSize();
    };

    resizeCalendarPage();
    window.addEventListener('resize', resizeCalendarPage);
    if ('ResizeObserver' in window) {
        new ResizeObserver(resizeCalendarPage).observe(header);
    }

    const tasks = []
    const events = []
    let blocks = []
    const blocksPromise = fetch(`/api/blocks/`)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`)
            return response.json()
        })
        .catch(error => {
            console.error("Error loading blocks:", error)
            return []
        })

    await Promise.all([
        fetch(`/api/tasks/`),
        fetch(`/api/events/`)
    ])
    .then(([tasksResponse, eventsResponse]) => Promise.all([tasksResponse.json(), eventsResponse.json()]))
    .then(([taskData, eventData]) => {
        taskData.forEach(task => {
        tasks.push({
            title:task.name,
            start:task.deadline,
            allDay: true,
            className: task.finished ? 'cal-task-done' : task.deadline >= new Date().toISOString().split('T')[0] ? 'cal-task-pending' : 'cal-task-overdue'
        })
    });
        eventData.forEach(event => {
            let end = event.end_time
            // FullCalendar treats `end` as exclusive: a zero-duration event
            // (start === end) would render invisible. Give it a frontend-only
            // 30-minute block so it stays visible; stored data stays faithful.
            if (new Date(event.start_time).getTime() === new Date(event.end_time).getTime()) {
                end = new Date(new Date(event.end_time).getTime() + 30 * 60000).toISOString()
            }
            events.push({
                title: event.name,
                start: event.start_time,
                end: end,
                className: 'cal-event'
                // allDay intentionally omitted → defaults to false (timed block)
            })
        });
    })
    .catch(error => console.error("Error al obtener datos:", error));
    blocks = await blocksPromise

    var calendarEl = document.getElementById('calendar');
    calendar = new FullCalendar.Calendar(calendarEl, {
        height: '100%',
        expandRows: true,
        views: {
            dayGridMonth: { dayMaxEventRows: true }
        },
        initialView: 'dayGridMonth',
        firstDay:1,
        locale: 'en',
        headerToolbar: {
        left: 'prev,next today',
        center: 'title',
        right: 'dayGridMonth,timeGridWeek,timeGridDay'
      },
      // The Block source returns no events in month view; refetch when switching
      // views or navigating so a cached month result cannot hide week/day Blocks.
      lazyFetching: false,
      eventTimeFormat: { hour: 'numeric', minute: '2-digit', hour12: false },
      slotLabelFormat: { hour: 'numeric', minute: '2-digit', hour12: false },
      eventSources: [
        tasks.concat(events),
        {
            events: (fetchInfo, successCallback) => {
                const visibleViews = ['timeGridWeek', 'timeGridDay']
                const currentViewType = calendar?.view?.type
                if (!visibleViews.includes(currentViewType) || blocks.length === 0) {
                    successCallback([])
                    return
                }

                const params = new URLSearchParams({
                    from: formatNaiveLocalDateTime(fetchInfo.start),
                    to: formatNaiveLocalDateTime(fetchInfo.end)
                })

                Promise.all(blocks.map(async block => {
                    try {
                        const response = await fetch(`/api/blocks/${block.id}/occurrences?${params}`)
                        if (!response.ok) throw new Error(`HTTP ${response.status}`)
                        const occurrences = await response.json()
                        return occurrences.map(occurrence => ({
                            title: block.name,
                            start: occurrence.start_time,
                            end: occurrence.end_time,
                            className: 'cal-block'
                        }))
                    } catch (error) {
                        console.error(`Error loading occurrences for block ${block.id}:`, error)
                        return []
                    }
                })).then(blockEvents => successCallback(blockEvents.flat()))
            }
        }
      ]
    });
    calendar.render();
    resizeCalendarPage();
}

function formatNaiveLocalDateTime(date) {
    const pad = value => String(value).padStart(2, '0')
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
        + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

document.addEventListener('DOMContentLoaded', async () => {
    LoadCalendar();
});
