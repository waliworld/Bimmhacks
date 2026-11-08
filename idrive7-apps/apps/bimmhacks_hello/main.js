'use strict';
/**
 * Bimmhacks Hello - the smallest useful app, meant to be copied.
 *
 * It draws one canvas screen, counts taps of the Count button, and reads two
 * settings (accent colour and large text) that come straight from app.json.
 * Everything an app needs is here and nothing else:
 *
 *   - mgu.run({ start(ctx) })   the app's entry point
 *   - ctx.canvas(W, H)          a surface to draw a frame on
 *   - ctx.loop('MAIN', fps, fn) redraw the MAIN screen at fps
 *   - ui.image('MAIN_GFX', cv)  push the frame to the screen
 *   - ui.onClick('BTN_*', fn)   a hardware / on-screen button
 *   - ctx.settings.get(key)     a value from app.json "settings"
 *
 * Build it with the kit that ships in this repo:
 *     python kit/tools/build.py bimmhacks_hello
 * Then install it from the Bimmhacks installer (Apps tab) or publish it to your
 * own GitHub release so the on-car Store can install it (docs/MAKE-YOUR-OWN-APP.md).
 */
const mgu = require('./kit/mgu');

const W = 1600, H = 520;
const ACCENTS = { blue: '#0b63d6', red: '#e2231a', green: '#2fd27a' };

mgu.run({
    async start(ctx) {
        const { ui, settings, log } = ctx;
        let count = 0;

        const render = () => {
            const accent = ACCENTS[settings.get('accent')] || ACCENTS.blue;
            const big = !!settings.get('big');
            const cv = ctx.canvas(W, H).clear('#0b0d12');
            cv.fillRect(0, 0, 10, H, accent);
            cv.text('BIMMHACKS', 56, 70, big ? 92 : 76, '#ffffff', { font: 'bold', spacing: 2 });
            cv.text('Hello from your own app', 60, big ? 190 : 170, big ? 44 : 36, '#9fb4d6');
            cv.text(String(count), 60, big ? 320 : 300, big ? 150 : 128, accent, { font: 'num' });
            cv.text('taps of Count', 60, big ? 410 : 380, 30, '#6b7280');
            cv.text('Count = add one   -   Settings = accent colour and text size', 60, H - 44, 24, '#6b7280');
            return ui.image('MAIN_GFX', cv);
        };

        ui.onClick('BTN_ACTION', () => { count += 1; log.info('count ' + count); return render(); });

        // redraw a couple of times a second; the counter changes on tap, not on a clock
        ctx.loop('MAIN', 2, render);
    },
});
