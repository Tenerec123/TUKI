async function LoadCalendar(){
    const calendarPage = document.querySelector('.kale-page');
    const header = document.querySelector('header');
    let calendar;
    let expandedBlockOverlay = null;
    let expandedBlockSource = null;
    let expandedOverlayOutsideHandler = null;
    let expandedOverlayEscapeHandler = null;

    function closeExpandedBlockOverlay() {
        if (!expandedBlockOverlay) return;
        expandedBlockOverlay.remove();
        expandedBlockOverlay = null;
        expandedBlockSource = null;
        document.removeEventListener('pointerdown', expandedOverlayOutsideHandler, true);
        document.removeEventListener('keydown', expandedOverlayEscapeHandler);
        window.removeEventListener('resize', closeExpandedBlockOverlay);
        window.removeEventListener('scroll', closeExpandedBlockOverlay, true);
    }

    function getBlockExpansionBounds(element) {
        const viewport = window.visualViewport;
        const viewportLeft = viewport?.offsetLeft ?? 0;
        const viewportTop = viewport?.offsetTop ?? 0;
        const viewportRight = viewportLeft + (viewport?.width ?? window.innerWidth);
        const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
        const clippingRect = element.closest('.fc-scroller')?.getBoundingClientRect()
            ?? calendarEl.getBoundingClientRect();
        const margin = 8;

        return {
            left: Math.max(viewportLeft, clippingRect.left) + margin,
            right: Math.min(viewportRight, clippingRect.right) - margin,
            top: Math.max(viewportTop, clippingRect.top) + margin,
            bottom: Math.min(viewportBottom, clippingRect.bottom) - margin
        };
    }

    function showExpandedBlockOverlay(info, sourceRect, bounds, contentWidth) {
        const overlay = info.el.cloneNode(true);
        overlay.style.cssText = '';
        overlay.classList.add('cal-block-expanded-overlay');
        overlay.removeAttribute('id');
        overlay.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
        overlay.setAttribute('role', 'group');
        overlay.setAttribute('aria-label', `${info.event.title} expanded event`);
        overlay.tabIndex = 0;

        const availableWidth = Math.max(1, bounds.right - bounds.left);
        const availableHeight = Math.max(1, bounds.bottom - bounds.top);
        const requestedWidth = Math.max(sourceRect.width * 1.12, contentWidth + 20, 144);
        const width = Math.min(requestedWidth, availableWidth);
        Object.assign(overlay.style, {
            position: 'fixed',
            left: `${bounds.left}px`,
            top: `${bounds.top}px`,
            right: 'auto',
            bottom: 'auto',
            width: `${width}px`,
            maxWidth: `${availableWidth}px`,
            height: 'auto',
            maxHeight: `${availableHeight}px`,
            margin: '0',
            overflow: 'auto',
            transform: 'none',
            transition: 'none',
            zIndex: '10000',
            boxSizing: 'border-box'
        });

        document.body.appendChild(overlay);
        const overlayRect = overlay.getBoundingClientRect();
        const maxLeft = Math.max(bounds.left, bounds.right - overlayRect.width);
        const maxTop = Math.max(bounds.top, bounds.bottom - overlayRect.height);
        overlay.style.left = `${Math.min(Math.max(sourceRect.left, bounds.left), maxLeft)}px`;
        overlay.style.top = `${Math.min(Math.max(sourceRect.top, bounds.top), maxTop)}px`;

        expandedBlockOverlay = overlay;
        expandedBlockSource = info.el;
        overlay.addEventListener('click', closeExpandedBlockOverlay);
        expandedOverlayOutsideHandler = event => {
            if (overlay.contains(event.target) || info.el.contains(event.target)) return;
            closeExpandedBlockOverlay();
        };
        expandedOverlayEscapeHandler = event => {
            if (event.key === 'Escape') closeExpandedBlockOverlay();
        };
        document.addEventListener('pointerdown', expandedOverlayOutsideHandler, true);
        document.addEventListener('keydown', expandedOverlayEscapeHandler);
        window.addEventListener('resize', closeExpandedBlockOverlay);
        window.addEventListener('scroll', closeExpandedBlockOverlay, true);
    }

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
    const blockEventSource = {
        id: 'block-occurrences',
        events: (fetchInfo, successCallback) => {
            const params = new URLSearchParams({
                from: formatNaiveLocalDateTime(fetchInfo.start),
                to: formatNaiveLocalDateTime(fetchInfo.end)
            })

            fetch(`/api/blocks/occurrences?${params}`)
                .then(response => {
                    if (!response.ok) throw new Error(`HTTP ${response.status}`)
                    return response.json()
                })
                .then(occurrences => successCallback(occurrences.map(occurrence => ({
                    title: occurrence.block_name,
                    start: occurrence.start_time,
                    end: occurrence.end_time,
                    className: 'cal-block'
                }))))
                .catch(error => {
                    console.error("Error loading Block occurrences:", error)
                    successCallback([])
                })
        }
    }

    var calendarEl = document.getElementById('calendar');
    calendar = new FullCalendar.Calendar(calendarEl, {
        height: '100%',
        expandRows: true,
        views: {
            dayGridMonth: { dayMaxEventRows: true },
            timeGridWeek: { dayHeaderFormat: { weekday: 'short' } }
        },
        initialView: 'dayGridMonth',
        firstDay:1,
        locale: 'en',
      headerToolbar: {
        left: 'prev,next today',
        center: 'title',
        right: 'dayGridMonth,timeGridWeek,timeGridDay'
      },
      datesSet: (dateInfo) => {
          closeExpandedBlockOverlay();
          calendarEl.querySelectorAll('.cal-block-expanded').forEach(eventEl => {
              eventEl.classList.remove('cal-block-expanded');
              eventEl.style.removeProperty('transition');
              eventEl.style.removeProperty('transform-origin');
              eventEl.style.removeProperty('--cal-block-expanded-scale');
          });

          const blocksSource = calendar.getEventSourceById('block-occurrences')
          const showBlocks = ['timeGridWeek', 'timeGridDay'].includes(dateInfo.view.type)

          if (showBlocks && !blocksSource) {
              calendar.addEventSource(blockEventSource)
          } else if (!showBlocks && blocksSource) {
              blocksSource.remove()
          }
      },
      eventClick: (info) => {
          if (window.innerWidth > 768) return
          // Expand any calendar event (tasks, events, blocks) on mobile, not
          // only Blocks. FullCalendar marks every event element with .fc-event.
          if (!info.el.classList.contains('fc-event')) return
          info.jsEvent.preventDefault()

          if (expandedBlockOverlay) {
              if (expandedBlockSource === info.el) {
                  closeExpandedBlockOverlay();
                  return;
              }
              closeExpandedBlockOverlay();
          }

          const wasExpanded = info.el.classList.contains('cal-block-expanded');
          calendarEl.querySelectorAll('.cal-block-expanded').forEach(eventEl => {
              eventEl.classList.remove('cal-block-expanded');
              eventEl.style.removeProperty('transition');
              eventEl.style.removeProperty('transform-origin');
              eventEl.style.removeProperty('--cal-block-expanded-scale');
          });
          if (wasExpanded) return;

          const collapsedRect = info.el.getBoundingClientRect();
          const bounds = getBlockExpansionBounds(info.el);
          const titleElement = info.el.querySelector('.fc-event-title');
          const timeElement = info.el.querySelector('.fc-event-time');
          const timeTextWidth = timeElement
              ? timeElement.textContent.trim().length * (parseFloat(getComputedStyle(timeElement).fontSize) || 12) * 0.65
              : 0;
          const contentWidth = Math.max(titleElement?.scrollWidth || 0, timeTextWidth);
          const desiredScale = 1.12;
          const needsMoreTextWidth = contentWidth + 16 > collapsedRect.width * desiredScale;

          if (needsMoreTextWidth) {
              showExpandedBlockOverlay(info, collapsedRect, bounds, contentWidth);
              return;
          }

          info.el.style.transition = 'none';
          info.el.style.transformOrigin = 'left top';
          info.el.style.setProperty('--cal-block-expanded-scale', '1');
          info.el.classList.add('cal-block-expanded');

          const rect = info.el.getBoundingClientRect();
          const expansionWouldClip =
              rect.right + rect.width * (desiredScale - 1) > bounds.right
              || rect.bottom + rect.height * (desiredScale - 1) > bounds.bottom
              || rect.left < bounds.left
              || rect.top < bounds.top;
          if (expansionWouldClip) {
              info.el.classList.remove('cal-block-expanded');
              info.el.style.removeProperty('transition');
              info.el.style.removeProperty('transform-origin');
              info.el.style.removeProperty('--cal-block-expanded-scale');
              showExpandedBlockOverlay(info, rect, bounds, contentWidth);
              return;
          }

          const fitAxis = (start, end, minimum, maximum, size, startOrigin, endOrigin) => {
              const startOriginRoom = maximum - end;
              const endOriginRoom = start - minimum;
              const requiredRoom = size * (desiredScale - 1);

              if (startOriginRoom >= requiredRoom) {
                  return { origin: startOrigin, scale: desiredScale };
              }
              if (endOriginRoom >= requiredRoom) {
                  return { origin: endOrigin, scale: desiredScale };
              }

              const useStartOrigin = startOriginRoom >= endOriginRoom;
              const availableRoom = Math.max(0, startOriginRoom, endOriginRoom);
              return {
                  origin: useStartOrigin ? startOrigin : endOrigin,
                  scale: Math.min(desiredScale, 1 + availableRoom / Math.max(size, 1))
              };
          };

          const horizontal = fitAxis(
              rect.left, rect.right, bounds.left, bounds.right, rect.width, 'left', 'right'
          );
          const vertical = fitAxis(
              rect.top, rect.bottom, bounds.top, bounds.bottom, rect.height, 'top', 'bottom'
          );
          requestAnimationFrame(() => {
              if (!info.el.classList.contains('cal-block-expanded')) return;
              info.el.style.removeProperty('transition');
              info.el.style.transformOrigin = `${horizontal.origin} ${vertical.origin}`;
              info.el.style.setProperty(
                  '--cal-block-expanded-scale',
                  String(Math.min(horizontal.scale, vertical.scale))
              );
          });
      },
      // The Block source returns no events in month view; refetch when switching
      // dates. Keeping the source out of month view also prevents cached Blocks
      // from leaking into that view.
      lazyFetching: false,
      eventTimeFormat: { hour: 'numeric', minute: '2-digit', hour12: false },
      slotLabelFormat: { hour: 'numeric', minute: '2-digit', hour12: false },
      eventSources: [tasks.concat(events)]
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
