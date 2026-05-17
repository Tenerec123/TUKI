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

    var datosBinarios = {
        "1771113600": 1, 
        "1771200000": 1, 
        "1771286400": 0  
    };

    var fechaInicio = moment().subtract(1, 'years').startOf('week').toDate();
    // Guardamos los límites exactos del año para la limpieza (hace 1 año justo y hoy justo)
    var limiteInferior = moment().subtract(1, 'years').startOf('day');
    var limiteSuperior = moment().subtract(3, 'days').startOf('day');

    var cal = new CalHeatMap();

    cal.init({
        itemSelector: "#cal-heatmap",
        domain: "week",
        subDomain: "day",
        rowLimit: 7,
        range: 54, 
        cellSize: 12,
        cellPadding: 2,
        start: fechaInicio, 
        data: datosBinarios,
        displayLegend: false,
        legend: [1],
        legendColors: ["#1f1f1f", "#ffff00"], 

        // onComplete asegura que los rectángulos existen en el DOM antes de ejecutar el filtro
        onComplete: function() {
        // Selecciona el contenedor de grupo <g> de cada día
        d3.selectAll("#cal-heatmap svg.graph-subdomain-group g")
            .filter(function(d) {
                // d contiene el objeto asignado por cal-heatmap {t: timestamp, v: valor}
                if (d && d.t) {
                    return d.t <= limiteInferior || d.t > limiteSuperior;
                }
                return false;
            })
            .remove(); // Elimina el elemento <g> completo del DOM
        },

        tooltip: true,
        subDomainTitleFormat: {
            empty: "No hecho: {date}",
            filled: "Hecho: {date}"
        },
        subDomainDateFormat: function(date) {
            return moment(date).format("LL");
        },

        onClick: function(date, value) {
            var fechaFormateada = moment(date).format("LL");
            var estado = (value >= 1) ? "Hecho" : "No hecho";
            alert("Fecha: " + fechaFormateada + "\nEstado: " + estado);
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