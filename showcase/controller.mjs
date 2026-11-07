const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export function createSelection(data) {
    let eventId = data.export.events[0]?.event_id ?? null;
    let caseName = data.demo.tamper_checks[0]?.case ?? null;
    let perspective = 'with_anchor', projectionName = 'full';
    const event = () => data.export.events.find(row => row.event_id === eventId) ?? null;
    return {
        state: () => ({
            eventId, caseName, perspective, projectionName
        }), event,
        verification: () => data.demo.tamper_checks.find(row => row.case === caseName)?.[perspective] ?? null,
        replay: () => data.demo['replay_' + projectionName],
        projection: () => data.projections[projectionName],
        selectEvent(id) {
            if (!data.export.events.some(row => row.event_id === id))
                throw Error('Unknown recorded event');
            eventId = id;
        },
        selectCase(value) {
            if (!data.demo.tamper_checks.some(row => row.case === value))
                throw Error('Unknown recorded alteration');
            caseName = value;
        },
        selectPerspective(value) {
            if (!['without_anchor', 'with_anchor'].includes(value))
                throw Error('Unknown anchor perspective');
            perspective = value;
        },
        selectProjection(value) {
            if (!['prefix', 'full'].includes(value))
                throw Error('Unknown recorded projection');
            projectionName = value;
        },
        move(delta) {
            const rows = data.export.events;
            if (rows.length)
                eventId = rows[clamp(rows.findIndex(row => row.event_id === eventId) + delta, 0, rows.length - 1)].event_id;
        }
    };
}
export function displayValue(value) {
    return value === null || value === undefined ? 'Not available' : String(value);
}
export function inkViewBox(metrics, baseline) {
    if (!metrics)
        return null;
    const { actualBoundingBoxLeft: left, actualBoundingBoxRight: right, actualBoundingBoxAscent: ascent, actualBoundingBoxDescent: descent } = metrics;
    if (![left, right, ascent, descent, baseline].every(Number.isFinite))
        return null;
    if (left + right <= 0 || ascent + descent <= 0)
        return null;
    return {
        x: -left, y: baseline - ascent, width: left + right, height: ascent + descent
    };
}
export function exactArtifact(artifacts, key) {
    if (!Object.hasOwn(artifacts, key) || typeof artifacts[key] !== 'string')
        throw Error('Unknown original artifact');
    return artifacts[key];
}
export function focusReturn(id) {
    return id === 'nav-export' ? 'menu-toggle' : id;
}
export function createMotion(reducedMotion = false) {
    let timeMs = 0, paused = reducedMotion, skipped = reducedMotion;
    return {
        state: () => ({
            timeMs, paused, reducedMotion, skipped
        }),
        tick(delta) {
            if (!paused && Number.isFinite(delta) && delta >= 0)
                timeMs += delta;
        },
        frame(value) {
            if (!Number.isFinite(value))
                throw Error('Frame must be finite');
            if (!reducedMotion)
                timeMs = Math.max(0, value);
        },
        pause(value) {
            paused = reducedMotion || Boolean(value);
        },
        reduce(value) {
            reducedMotion = Boolean(value);
            if (reducedMotion) {
                paused = true;
                skipped = true;
            }
        },
        skipIntro() {
            skipped = true;
        }
    };
}
export function motionPose(state) {
    const t = state.timeMs;
    return {
        intro: state.skipped ? 1 : clamp(t / 1800, 0, 1), ticker: (t % 30000) / 30000,
        reelAngle: Math.sin(t / 1100) * 6, binderAngle: Math.sin(t / 850 + .8) * 6,
        reelY: Math.sin(t / 1300) * 8, binderY: Math.cos(t / 1000) * 7
    };
}
