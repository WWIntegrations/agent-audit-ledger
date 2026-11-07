import { createSelection, createMotion, motionPose, exactArtifact, displayValue, focusReturn, inkViewBox } from './controller.mjs';
import { createArtwork } from './artwork.js';
const $ = id => document.getElementById(id);
const data = JSON.parse($('showcase-data').textContent);
const originals = JSON.parse($('showcase-artifacts').textContent);
const build = JSON.parse($('build-info').textContent);
const preference = matchMedia('(prefers-reduced-motion: reduce)');
const motion = createMotion(preference.matches), selection = data ? createSelection(data) : null;
let ready = false, artwork = null, manual = false, last = null, menuOpen = true, screenReveal = 0, fallbackReason = null;
const openers = new Map();
const node = (tag, text) => {
    const value = document.createElement(tag);
    if (text !== undefined)
        value.textContent = String(text);
    return value;
};
const put = (id, text) => {
    $(id).textContent = String(text);
};
const pretty = value => JSON.stringify(value, null, 2);
function facts(container, object) {
    const dl = node('dl');
    for (const [key, value] of Object.entries(object))
        dl.append(node('dt', key.replaceAll('_', ' ')), node('dd', displayValue(value)));
    if (container.tagName === 'DL') container.replaceChildren(...dl.childNodes);
    else container.replaceChildren(dl);
}
function verification(container, result) {
    container.dataset.chain = result.chain;
    container.dataset.anchor = result.anchor;
    container.dataset.failureCode = result.failure?.code ?? '';
    facts(container, {
        chain: result.chain, anchor: result.anchor, snapshot_seq: result.snapshot_seq,
        anchored_seq: result.anchored_seq, unanchored_tail: result.unanchored_tail,
        failure: result.failure?.code ?? 'No failure reported', failure_seq: result.failure?.seq ?? null
    });
}
function renderEvents() {
    const event = selection.event();
    $('event-select').value = event?.event_id ?? '';
    $('event-detail').dataset.seq = event?.seq ?? '';
    put('event-title', event ? `${event.seq}. ${event.type}` : 'No recorded events');
    if (event) {
        facts($('event-facts'), {
            event_id: event.event_id, recorded_at: event.recorded_at, prev_hash: event.prev_hash, event_hash: event.event_hash
        });
        put('event-payload', pretty(event.payload));
        put('event-json', pretty(event));
    }
    else {
        $('event-facts').replaceChildren();
        put('event-payload', '');
        put('event-json', '');
    }
    $('previous-event').disabled = !event || event === data.export.events[0];
    $('next-event').disabled = !event || event === data.export.events.at(-1);
    for (const button of $('event-list').querySelectorAll('button'))
        button.setAttribute('aria-current', String(button.dataset.eventId === event?.event_id));
}
function renderProjection() {
    const receipt = selection.replay(), projection = selection.projection();
    $('projection-result').dataset.throughSeq = receipt.through_seq;
    facts($('projection-result'), {
        through_seq: receipt.through_seq, ...receipt.counts, source_origin: receipt.provenance.source_origin
    });
    put('projection-provenance', `Recorded projection / ${receipt.provenance.source_origin}. New SQLite was reopened by the core; this viewer does not replay effects.`);
    $('projection-rows').replaceChildren();
    for (const [kind, rows] of Object.entries({
        runs: projection.state.runs, artifacts: projection.state.artifacts
    })) {
        const heading = node('h3', kind);
        $('projection-rows').append(heading);
        for (const row of rows) {
            const article = node('article');
            article.dataset.kind = kind;
            article.dataset.rowId = row.artifact_id ?? row.run_id;
            article.append(node('pre', pretty(row)));
            $('projection-rows').append(article);
        }
        if (!rows.length)
            $('projection-rows').append(node('p', 'No rows in this recorded prefix.'));
    }
    put('projection-json', pretty({
        receipt, projection
    }));
    facts($('phone-counts'), {
        runs: receipt.counts.runs, artifacts: receipt.counts.artifacts, finished: receipt.counts.finished_runs
    });
}
function renderTamper() {
    const result = selection.verification();
    verification($('tamper-result'), result);
    put('tamper-json', pretty(result));
}
function renderData() {
    for (const event of data.export.events) {
        const option = node('option', `${event.seq}. ${event.type} / ${event.event_id}`);
        option.value = event.event_id;
        $('event-select').append(option);
        const li = node('li'), button = node('button', `${event.seq}. ${event.type}`);
        button.dataset.eventId = event.event_id;
        button.addEventListener('click', () => {
            selection.selectEvent(event.event_id);
            renderEvents();
        });
        li.append(button);
        $('event-list').append(li);
        const phoneLi = node('li');
        phoneLi.append(node('strong', `${event.seq} / ${event.type.replaceAll('_', ' ')}`));
        $('phone-events').append(phoneLi);
    }
    for (const item of data.demo.tamper_checks) {
        const option = node('option', item.case.replaceAll('_', ' '));
        option.value = item.case;
        $('tamper-select').append(option);
    }
    verification($('verify-result'), data.demo.initial_verification);
    put('phone-ledger', `${data.export.count} original records`);
    put('phone-chain', `${data.demo.initial_verification.chain} / anchor ${data.demo.initial_verification.anchor}`);
    put('checkpoint-json', pretty(data.checkpoint));
    put('manifest-json', pretty(data.manifest));
    put('ledger-header', pretty({header: data.export.header, provenance: data.export.provenance}));
    put('source-mode', `${build.mode}. Ledger origin: ${data.export.provenance.source_origin}. No new ledger operations occur in this viewer.`);
    for (const item of data.demo.artifact_checks) {
        const article = node('article');
        article.dataset.artifactId = item.artifact_id;
        article.dataset.matches = String(item.matches);
        article.append(node('h3', item.artifact_id));
        const values = node('div');
        facts(values, item);
        article.append(values);
        $('artifact-checks').append(article);
    }
    for (const key of Object.keys(originals)) {
        const option = node('option', key);
        option.value = key;
        $('export-kind').append(option);
    }
    renderEvents();
    renderProjection();
    renderTamper();
}
function setMenu(value, returnFocus = false) {
    menuOpen = Boolean(value);
    $('primary-nav').hidden = !menuOpen;
    $('menu-toggle').setAttribute('aria-expanded', String(menuOpen));
    $('menu-toggle').setAttribute('aria-label', menuOpen ? 'Collapse navigation' : 'Expand navigation');
    if (returnFocus)
        $('menu-toggle').focus({
            preventScroll: true
        });
}
function openDialog(id, trigger) {
    const opener = focusReturn(trigger.id);
    if (trigger.id === 'nav-export')
        setMenu(false);
    openers.set(id, $(opener));
    $(id).showModal();
}
for (const [id, closeId] of [['export-dialog', 'export-close'], ['projection-dialog', 'projection-close']]) {
    $(closeId).addEventListener('click', () => $(id).close());
    $(id).addEventListener('close', () => openers.get(id)?.focus({
        preventScroll: true
    }));
}
function download(raw, filename) {
    const url = URL.createObjectURL(new Blob([raw], {
        type: 'application/json'
    }));
    const link = node('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
}
$('menu-toggle').addEventListener('click', () => setMenu(!menuOpen));
document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !document.querySelector('dialog[open]') && menuOpen)
        setMenu(false, true);
});
for (const link of document.querySelectorAll('a[href^="#"]'))
    link.addEventListener('click', () => {
        motion.skipIntro();
        if (link.closest('#primary-nav'))
            setMenu(false);
        const target = document.querySelector(link.getAttribute('href'));
        if (target) {
            target.tabIndex = -1;
            requestAnimationFrame(() => target.focus({
                preventScroll: true
            }));
        }
    });
$('motion-toggle').addEventListener('click', () => {
    manual = false;
    motion.pause(!motion.state().paused);
    last = null;
    paint();
});
preference.addEventListener('change', event => {
    motion.reduce(event.matches);
    if (event.matches)
        screenReveal = 1;
    last = null;
    paint();
});
document.addEventListener('focusin', event => {
    if (!ready)
        return;
    motion.skipIntro();
    if ($('screens').contains(event.target)) screenReveal = 1;
    paint();
});
if (data) {
    $('event-select').addEventListener('change', event => {
        selection.selectEvent(event.target.value);
        renderEvents();
    });
    for (const [id, delta] of [['previous-event', -1], ['next-event', 1]])
        $(id).addEventListener('click', () => {
            selection.move(delta);
            renderEvents();
        });
    $('tamper-select').addEventListener('change', event => {
        selection.selectCase(event.target.value);
        renderTamper();
    });
    $('anchor-perspective').addEventListener('change', event => {
        selection.selectPerspective(event.target.value);
        renderTamper();
    });
    $('projection-select').addEventListener('change', event => {
        selection.selectProjection(event.target.value);
        renderProjection();
    });
    for (const id of ['nav-export', 'hero-export', 'collage-export', 'export-button'])
        $(id).addEventListener('click', () => openDialog('export-dialog', $(id)));
    $('projection-details').addEventListener('click', () => openDialog('projection-dialog', $('projection-details')));
    $('phone-inspect').addEventListener('click', () => {
        $('events').scrollIntoView();
        $('event-select').focus({
            preventScroll: true
        });
    });
    $('export-artifact').addEventListener('click', () => {
        const key = $('export-kind').value;
        download(exactArtifact(originals, key), key + '.json');
        put('export-status', 'Downloaded original ' + key + '.json.');
    });
}
$('license-export').addEventListener('click', () => download($('font-license').textContent, 'font-license.json'));
function geometry() {
    const bounds = $('screens').getBoundingClientRect();
    if (!motion.state().paused)
        screenReveal = Math.min(1, Math.max(0, (innerHeight - bounds.top) / (innerHeight * .6)));
    if (motion.state().reducedMotion)
        screenReveal = 1;
    if (scrollY > 400 && innerWidth > 1100 && menuOpen && !$('primary-nav').contains(document.activeElement))
        setMenu(false);
    paint();
}
function paint() {
    const state = motion.state(), pose = motionPose(state);
    const trackWidth = $('notice-track').firstElementChild.getBoundingClientRect().width;
    $('notice-track').style.transform = `translateX(${-pose.ticker * trackWidth}px)`;
    $('hero-word').style.clipPath = `inset(${(1 - pose.intro) * 100}% 0 0 0)`;
    document.querySelector('.reel').style.transform = `translateY(${pose.reelY}px) rotate(${pose.reelAngle}deg)`;
    document.querySelector('.binder').style.transform = `translateY(${pose.binderY}px) rotate(${pose.binderAngle}deg)`;
    const offset = innerWidth <= 700 ? 132 : 288;
    $('screen-pair').style.transform = `translateY(${(1 - screenReveal) * offset}px) scale(${.75 + .25 * screenReveal})`;
    $('screen-pair').style.opacity = String(screenReveal);
    $('motion-toggle').disabled = state.reducedMotion;
    $('motion-toggle').setAttribute('aria-pressed', String(state.paused));
    put('motion-toggle', state.reducedMotion ? 'Still artwork' : state.paused ? 'Resume motion' : 'Pause motion');
    document.querySelector('.header').style.top = `${Math.max(innerWidth <= 700 ? 20 : 24, (innerWidth <= 700 ? 64 : 72) - scrollY)}px`;
}
function fallback(reason) {
    fallbackReason = reason;
    artwork?.dispose();
    artwork = null;
    $('link-canvas').hidden = true;
    $('art-fallback').removeAttribute('hidden');
    $('fallback-notice').hidden = false;
}
function meta() {
    return {
        ready, ...selection?.state(), timeMs: motion.state().timeMs, motionPaused: motion.state().paused,
        reducedMotion: motion.state().reducedMotion, menuOpen, sourceMode: build.mode, preview: !data,
        scrollY, screenReveal, ...(artwork?.stats() ?? {
            renderer: 'SVG fallback', reason: fallbackReason
        })
    };
}
Object.defineProperty(window, 'showcaseMeta', {
    get: meta
});
window.showcaseFrame = ms => {
    manual = true;
    motion.frame(ms);
    paint();
    return meta();
};
window.showcaseScene = ({ timeMs = motion.state().timeMs, scrollY: y = scrollY } = {}) => {
    if (!Number.isFinite(y))
        throw Error('Scroll must be finite');
    manual = true;
    motion.frame(timeMs);
    window.scrollTo({
        top: Math.max(0, y), behavior: 'instant'
    });
    geometry();
    return meta();
};
window.selectLedgerEvidence = ({ eventId, caseName, perspective, projectionName } = {}) => {
    if (!selection)
        throw Error('Artwork-only preview has no ledger');
    if (eventId !== undefined)
        selection.selectEvent(eventId);
    if (caseName !== undefined) {
        selection.selectCase(caseName);
        $('tamper-select').value = caseName;
    }
    if (perspective !== undefined) {
        selection.selectPerspective(perspective);
        $('anchor-perspective').value = perspective;
    }
    if (projectionName !== undefined) {
        selection.selectProjection(projectionName);
        $('projection-select').value = projectionName;
    }
    renderEvents();
    renderTamper();
    renderProjection();
    return meta();
};
window.addEventListener('resize', () => {
    artwork?.resize();
    geometry();
});
window.addEventListener('scroll', geometry, {
    passive: true
});
$('link-canvas').addEventListener('contextlost', event => {
    event.preventDefault();
    fallback('Canvas context lost');
});
window.showcaseReady = (async () => {
    if (data)
        renderData();
    else {
        put('init-status', 'Artwork-only preview. No ledger results are present.');
        for (const button of document.querySelectorAll('button:not(#menu-toggle):not(#motion-toggle):not(#license-export)'))
            button.disabled = true;
    }
    await document.fonts.load('500px Anton');
    await document.fonts.ready;
    const glyphs = $('hero-glyphs');
    const measure = document.createElement('canvas').getContext('2d');
    let box = glyphs.getBBox();
    if (measure) {
        const style = getComputedStyle(glyphs);
        measure.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        measure.textAlign = 'left';
        measure.textBaseline = 'alphabetic';
        box = inkViewBox(measure.measureText(glyphs.textContent), Number(glyphs.getAttribute('y'))) ?? box;
    }
    if (box.width > 0 && box.height > 0)
        $('hero-word').setAttribute('viewBox', `${box.x} ${box.y} ${box.width} ${box.height}`);
    try {
        artwork = createArtwork($('link-canvas'));
        if (!artwork)
            fallback('Canvas2D unavailable');
    }
    catch (error) {
        fallback(error.message);
    }
    ready = true;
    if (data) {
        for (const id of ['event-select', 'tamper-select', 'anchor-perspective', 'projection-select'])
            $(id).disabled = false;
        $('init-status').hidden = true;
    }
    geometry();
    return meta();
})();
function animate(now) {
    if (last !== null && ready && !manual && !document.hidden) {
        motion.tick(Math.min(100, now - last));
        paint();
    }
    last = now;
    requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
