const SERVER_IP = window.location.hostname;
const API_PORT = window.location.port || "8000"
window.API_URL = `http://${SERVER_IP}:${API_PORT}`;

async function LoadCalendar(){
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
}

async function LoadHeatMap(){
    var checkData = {};
    await fetch(`${window.API_URL}/api/routines/stats/${2}`)
    .then(response => response.json())
    .then(data => {
        data.forEach(check => {
            console.log(check)
            const [dia, mes, año] = check.check_date.split('/');
            const unix = `${Math.floor(+new Date(`${año}-${mes}-${dia}`) / 1000)}`;
            checkData[unix] = 1
        });
    });
    console.log(checkData)

    var initDate = moment().subtract(1, 'years').startOf('week').toDate();
    var lowLimit = moment().subtract(1, 'years').startOf('day');
    var upLimit = moment().startOf('day');

    var cal = new CalHeatMap();

    cal.init({
        itemSelector: "#cal-heatmap",
        domain: "week",
        subDomain: "day",
        rowLimit: 7,
        range: 54, 
        cellSize: 12,
        cellPadding: 2,
        start: initDate, 
        data: checkData,
        displayLegend: false,
        legend: [1],
        legendColors: ["#1f1f1f", "#ffff00"], 
        onComplete: function() {
        d3.selectAll("#cal-heatmap svg.graph-subdomain-group g")
            .filter(function(d) {
                if (d && d.t) {
                    return d.t <= lowLimit || d.t > upLimit;
                }
                return false;
            })
            .remove();
        },

        tooltip: true,
        subDomainTitleFormat: {
            empty: "{date}",
            filled: "{date}"
        },
        subDomainDateFormat: function(date) {
            return moment(date).format("LL");
        },

        domainLabelFormat: function(date) {
            var m = moment(date);
            if (m.date() <= 7) {
                return m.format("MMM");
            }
            return "";
        }
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    LoadCalendar();
    LoadHeatMap();
});