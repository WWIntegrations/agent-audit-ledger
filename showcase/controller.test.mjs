import test from 'node:test';
import assert from 'node:assert/strict';
import { createSelection, createMotion, motionPose, exactArtifact, displayValue, focusReturn, inkViewBox } from './controller.mjs';
const bundle = () => ({
    export: {
        events: [{
                event_id: 'start', seq: 1
            }, {
                event_id: 'end', seq: 2
            }]
    }, demo: {
        initial_verification: {
            chain: 'consistent', anchor: 'none'
        }, tamper_checks: [{
                case: 'tail_truncated', without_anchor: {
                    chain: 'consistent', anchor: 'none'
                }, with_anchor: {
                    chain: 'invalid', anchor: 'failed', failure: {
                        code: 'truncated_before_anchor', seq: null
                    }
                }
            }], replay_prefix: {
            through_seq: 1, counts: {
                active_runs: 1
            }
        }, replay_full: {
            through_seq: 2, counts: {
                active_runs: 0
            }
        }
    }, projections: {
        prefix: {
            state: {
                runs: [{
                        outcome: null
                    }]
            }
        }, full: {
            state: {
                runs: [{
                        outcome: 'success'
                    }]
            }
        }
    }
});
test('event selection stays inside recorded list', () => {
    const s = createSelection(bundle());
    assert.equal(s.event().event_id, 'start');
    s.selectEvent('end');
    assert.equal(s.event().seq, 2);
    assert.throws(() => s.selectEvent('absent'));
    assert.equal(s.event().seq, 2);
});
test('tamper and anchor selection preserve the same case', () => {
    const s = createSelection(bundle());
    s.selectPerspective('without_anchor');
    assert.equal(s.verification().anchor, 'none');
    s.selectPerspective('with_anchor');
    assert.equal(s.verification().failure.seq, null);
    assert.throws(() => s.selectCase('unknown'));
    assert.throws(() => s.selectPerspective('forged'));
});
test('replay selects real receipt and SQL state as a pair', () => {
    const s = createSelection(bundle());
    s.selectProjection('prefix');
    assert.equal(s.replay().through_seq, 1);
    assert.equal(s.projection().state.runs[0].outcome, null);
    s.selectProjection('full');
    assert.equal(s.replay().counts.active_runs, 0);
    assert.equal(s.projection().state.runs[0].outcome, 'success');
    assert.throws(() => s.selectProjection('quarter'));
});
test('movement clamps at actual events', () => {
    const s = createSelection(bundle());
    s.move(-1);
    assert.equal(s.event().seq, 1);
    s.move(99);
    assert.equal(s.event().seq, 2);
});
test('null and false are not positive status or zero', () => {
    assert.equal(displayValue(null), 'Not available');
    assert.equal(displayValue(false), 'false');
    assert.equal(displayValue(0), '0');
});
test('exact export keeps whitespace, large safe integer and escaping', () => {
    const raw = ' {"value":9007199254740991,"text":"<script>"}\n';
    assert.equal(exactArtifact({
        export: raw
    }, 'export'), raw);
    assert.throws(() => exactArtifact({}, 'toString'));
});
test('paused clock resumes from same phase', () => {
    const m = createMotion();
    m.tick(1000);
    m.pause(true);
    m.tick(200);
    assert.equal(m.state().timeMs, 1000);
    m.pause(false);
    m.tick(40);
    assert.equal(m.state().timeMs, 1040);
});
test('frame output is independent of history', () => {
    const a = createMotion(), b = createMotion();
    a.frame(15000);
    a.frame(15002);
    b.frame(0);
    b.frame(15002);
    assert.deepEqual(motionPose(a.state()), motionPose(b.state()));
});
test('initial reduced motion freezes frame and reveals text', () => {
    const m = createMotion(true);
    const a = motionPose(m.state());
    m.frame(1500);
    m.tick(500);
    assert.deepEqual(motionPose(m.state()), a);
    assert.equal(a.intro, 1);
});
test('dynamic reduced motion freezes all loops and returns paused', () => {
    const m = createMotion();
    m.frame(4000);
    m.reduce(true);
    const p = motionPose(m.state());
    m.frame(9000);
    m.tick(1000);
    assert.deepEqual(motionPose(m.state()), p);
    m.reduce(false);
    m.tick(1000);
    assert.equal(m.state().timeMs, 4000);
    assert.equal(m.state().paused, true);
});
test('invalid frame cannot corrupt time', () => {
    const m = createMotion();
    m.frame(55);
    assert.throws(() => m.frame(NaN));
    assert.equal(m.state().timeMs, 55);
});
test('skip intro and menu focus are explicit adaptations', () => {
    const m = createMotion();
    m.skipIntro();
    assert.equal(motionPose(m.state()).intro, 1);
    assert.equal(focusReturn('nav-export', true), 'menu-toggle');
    assert.equal(focusReturn('export-button', false), 'export-button');
});
test('hero crops actual ink instead of font line box', () => {
    const metrics = {
        actualBoundingBoxLeft: -8, actualBoundingBoxRight: 1200, actualBoundingBoxAscent: 360, actualBoundingBoxDescent: 5
    };
    assert.deepEqual(inkViewBox(metrics, 430), {
        x: 8, y: 70, width: 1192, height: 365
    });
    assert.equal(inkViewBox(null, 430), null);
    assert.equal(inkViewBox({
        actualBoundingBoxLeft: NaN
    }, 430), null);
});
test('closing export opened from desktop navigation returns to the visible menu toggle', () => {
    // Navigation export collapses its parent on every viewport. The opener
    // itself is therefore hidden even when the desktop menu was expanded.
    assert.equal(focusReturn('nav-export', false), 'menu-toggle');
    assert.equal(focusReturn('hero-export', false), 'hero-export');
    assert.equal(focusReturn('projection-details', false), 'projection-details');
});
