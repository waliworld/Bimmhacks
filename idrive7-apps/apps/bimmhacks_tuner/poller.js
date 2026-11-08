'use strict';
/**
 * poller.js - reads the engine unit's values one request at a time and keeps the latest of each.
 *
 *   const p = new Poller({ reader: new DidReader(link, { ecu: 0x12 }), link, ecu: 0x12 });
 *   p.setChannels(channels);                 // channels: pids.js / channels.js records (cand: [...] or raw: {...})
 *   for (;;) { const wait = await p.step(); if (wait) await sleep(wait); }
 *   p.values()                               // { key: number | 'HEX' } of the values that are still fresh
 *   p.status() / p.summary()                 // which values the car answers (for the PIDs / Datalog pages)
 *
 * What it does about the real world:
 *   rounds        every round reads all "fast" channels and a few "slow" ones in rotation (slowPerCycle),
 *                 so a long list of slow values cannot starve boost and lambda.
 *   silent unit   kit/readonly-diag.js marks a unit that missed 5 requests in a row as silent and then answers
 *                 nothing at all until wake() - the car asleep, the ignition just on, a value it never answers.
 *                 The poller wakes it every wakeMs, so the gauges and the log come back by themselves.
 *   refusals      a value the unit refuses (NRC: it does not have it) is asked again after REFUSED_MS only;
 *                 one that gets no reply backs off 5 s, 10 s ... up to 60 s. Neither holds up the others.
 *   short waits   a candidate waits 1.2 s for an answer (kit/dids.js would wait 4 s), so one dead value costs a
 *                 moment, not the round.
 *   fresh only    values() returns a value only while it is younger than ~2.5 of its own read intervals
 *                 (never less than 4 s): a log row never repeats an old number as if it were current.
 * Everything goes through the read-only link, so nothing here can send anything but a read.
 */
const REFUSED_MS = 120000;
const SILENT_MS = [5000, 10000, 20000, 40000, 60000];
const MIN_AGE_MS = 4000;
const RAW_TIMEOUT_MS = 1200;

const hex2 = (b) => (b < 16 ? '0' : '') + b.toString(16).toUpperCase();

class Poller {
    constructor(opts) {
        this.reader = opts.reader;
        this.link = opts.link;
        this.log = opts.log || { info() {}, warn() {} };
        this.now = opts.now || Date.now;
        this.ecu = opts.ecu == null ? 0x12 : opts.ecu;
        this.slowPerCycle = opts.slowPerCycle || 3;
        this.wakeMs = opts.wakeMs || 10000;
        this.channels = [];
        this.byKey = {};
        this.st = {};                 // key -> { v, t, ok, why, fails, until, tried }
        this.queue = [];
        this.slowAt = 0;
        this.cycles = 0;
        this.cycleMs = 0;             // the round's length, smoothed
        this.cycleStart = 0;
        this.readsThisCycle = 0;
        this.okThisCycle = 0;         // answers in this round: only such rounds measure the rate
        this.silentSince = {};        // unit -> when the link first gave it up (it is woken wakeMs later)
        this.stats = { reads: 0, ok: 0, refused: 0, silent: 0, wakes: 0 };
    }

    /** Replace the channel list; what is already known about a value that stays is kept. */
    setChannels(list) {
        this.channels = list.slice();
        this.byKey = {};
        const st = {};
        list.forEach((c) => {
            this.byKey[c.key] = c;
            st[c.key] = this.st[c.key] || { v: null, t: 0, ok: false, why: '', fails: 0, until: 0, tried: false };
        });
        this.st = st;
        this.queue = [];
        this.slowAt = 0;
    }

    _ecuOf(ch) {
        if (ch.ecu != null) return ch.ecu;
        const c = ch.cand && ch.cand[0];
        return c && c.ecu != null ? c.ecu : this.ecu;
    }

    _plan() {
        const fast = this.channels.filter((c) => c.rate === 'fast');
        const slow = this.channels.filter((c) => c.rate !== 'fast');
        const take = [];
        const n = Math.min(this.slowPerCycle, slow.length);
        for (let i = 0; i < n; i++) take.push(slow[(this.slowAt + i) % slow.length]);
        this.slowAt = slow.length ? (this.slowAt + this.slowPerCycle) % slow.length : 0;
        return fast.concat(take).map((c) => c.key);
    }

    /** Why a scaled value gave nothing, from what the reader noted for its candidates. */
    _why(ch) {
        let refused = null, range = false;
        for (const c of ch.cand || []) {
            const r = this.reader.raws[this.reader.key(c)];
            if (!r) continue;
            if (r.why === 'no reply') return 'no reply';
            if (/^NRC/.test(r.why)) refused = refused || r.why;
            else if (r.why === 'out of range') range = true;
        }
        // answered but implausible beats refused: the car HAS the value, so a wrong scale shows up as such
        return range ? 'out of range' : refused ? 'refused (' + refused + ')' : 'no answer';
    }

    async _readRaw(ch, ecu) {
        let uds;
        try { uds = await this.link.read(ecu, parseInt(ch.raw.did, 16), RAW_TIMEOUT_MS); } catch (e) { return { why: 'blocked: ' + (e.message || e) }; }
        if (uds === undefined) return { why: 'cancelled' };
        if (!uds) return { why: 'no reply' };
        if (uds[0] === 0x7F) return { why: 'refused (NRC ' + hex2(uds[2]) + ')' };
        if (uds[0] !== 0x62 || uds.length < 3) return { why: 'unexpected reply' };
        const from = 3 + (ch.raw.at || 0);
        const data = uds.slice(from, from + ch.raw.len);
        return data.length ? { v: data.map(hex2).join('') } : { why: 'empty answer' };
    }

    /**
     * One request (or none, when it has to wait). Resolves with how long the caller should wait before the
     * next call, in ms: 0 to carry straight on.
     */
    async step() {
        if (!this.channels.length) return 500;
        const t0 = this.now();
        if (!this.queue.length) {
            if (this.cycleStart && this.okThisCycle > 0) {             // a round the car answered nothing in says nothing about the rate
                const d = t0 - this.cycleStart;
                this.cycleMs = this.cycleMs ? 0.7 * this.cycleMs + 0.3 * d : d;
                this.cycles++;
            }
            this.cycleStart = t0;
            this.readsThisCycle = 0;
            this.okThisCycle = 0;
            this.queue = this._plan();
        }
        const key = this.queue.shift();
        const ch = this.byKey[key], st = this.st[key];
        const pace = () => (!this.queue.length && this.readsThisCycle === 0 ? 250 : 0);
        if (!ch || st.until > t0) return pace();

        const ecu = this._ecuOf(ch);
        if (this.link.silent && this.link.silent(ecu)) {
            // the link answers nothing for a unit it gave up on: wait wakeMs from when that was first noticed, then ask again
            if (this.silentSince[ecu] === undefined) this.silentSince[ecu] = t0;
            if (t0 - this.silentSince[ecu] < this.wakeMs) return pace();
            this.link.wake(ecu);
            delete this.silentSince[ecu];
            this.stats.wakes++;
            this.log.info('poller: unit ' + ecu.toString(16) + ' was silent - asking again');
        } else if (this.silentSince[ecu] !== undefined) {
            delete this.silentSince[ecu];
        }

        let v = null, why = '';
        if (ch.raw) {
            const r = await this._readRaw(ch, ecu);
            v = r.v === undefined ? null : r.v;
            why = r.why || '';
        } else {
            v = await this.reader.value(ch.key, ch.cand);
            if (v === null) why = this._why(ch);
        }
        this.readsThisCycle++;
        this.stats.reads++;
        st.tried = true;
        const now = this.now();
        if (v !== null) {
            st.v = v; st.t = now; st.ok = true; st.why = ''; st.fails = 0; st.until = 0;
            this.stats.ok++;
            this.okThisCycle++;
        } else {
            st.ok = false; st.why = why; st.fails++;
            const refused = /^refused/.test(why);
            st.until = now + (refused ? REFUSED_MS : SILENT_MS[Math.min(st.fails, SILENT_MS.length) - 1]);
            if (refused) this.stats.refused++; else this.stats.silent++;
        }
        return 0;
    }

    /** The latest value of each channel that is still fresh. */
    values() {
        const now = this.now(), out = {};
        const nSlow = this.channels.filter((c) => c.rate !== 'fast').length;
        const slowPeriod = this.cycleMs * Math.max(1, Math.ceil(nSlow / this.slowPerCycle));
        this.channels.forEach((ch) => {
            const st = this.st[ch.key];
            if (!st || !st.ok) return;
            const period = ch.rate === 'fast' ? this.cycleMs : slowPeriod;
            if (now - st.t <= Math.max(MIN_AGE_MS, 2.5 * period)) out[ch.key] = st.v;
        });
        return out;
    }

    /** Per visible channel: { key, label, pack, state, why }. state: ok | stale | refused | range | no reply | waiting. */
    status() {
        const fresh = this.values();
        return this.channels.filter((c) => !c.hidden).map((ch) => {
            const st = this.st[ch.key];
            let state = 'waiting';
            if (st.ok) state = fresh[ch.key] !== undefined ? 'ok' : 'stale';
            else if (st.tried) state = /^refused/.test(st.why) ? 'refused' : st.why === 'out of range' ? 'range' : 'no reply';
            return { key: ch.key, label: ch.label, pack: ch.pack, state, why: st.why };
        });
    }

    /** { pack: { total, answering } } over the visible channels. */
    summary() {
        const out = {};
        this.status().forEach((s) => {
            const p = out[s.pack] || (out[s.pack] = { total: 0, answering: 0 });
            p.total++;
            if (s.state === 'ok') p.answering++;
        });
        return out;
    }

    /** Fast channels' updates per second, as measured (0 until a round has been timed). */
    get rateHz() { return this.cycleMs ? Math.round(10000 / this.cycleMs) / 10 : 0; }
}

module.exports = { Poller, REFUSED_MS, SILENT_MS, MIN_AGE_MS };
