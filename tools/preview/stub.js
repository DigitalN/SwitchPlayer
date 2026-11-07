// Stand-in for the app's Api (the pywebview bridge), so index.html runs in a browser (see
// serve.py). The games are the preview Library folder's, named and with art as the app finds
// them; their states are made up, and Build, Stop and the queue are simulated, a stage's
// second standing for a minute.
//
// Query options, combined with commas: ?s=building,dark
//   library: (default: as it is now, nothing built yet) building ready failed noart many empty nofolder readme
//            preparing (Ocarina downloading nxnative, held still) installing (Ocarina installing the Python
//            packages, held still) ocarina (Ocarina at its shaders, held still)
//   setup:   (default: everything a Build needs is ready, so Build Setup opens only from the menu:
//            run showSetup() to see it) setup-missing (something is missing, so it opens at launch)
//            install-fails (the Build tools are missing, and Install Build Tools can't install them)
//   update:  the App update toast, held still: update-checking update-downloading update-verifying
//            update-ready update-held (held for a Build) update-installing update-uptodate
//            update-updated (the launch after an update) update-off (outside Applications)
//            update-failed (Check for Updates… couldn't: stays until closed) update-failed-auto (fades)
//            update-run (a whole automatic update, from Checking to Updated) update-none (Checking, then Up to date!)
//   pane:    closed (the Build Log pane hidden, as a player may leave it)
//            copy-fails (Copy Log can't put the log on the clipboard)
//   theme:   dark light
// Change window.__state while it runs to try anything else; the next poll picks it up.
// window.__state.calls is every API call the page made. Page errors that the app would log
// go to the console.
(() => {
  const app = window.__app;
  const fresh = g => ({
    id: g.id, name: g.name, title_id: g.title_id, art: g.art_data ? `${g.title_id}:1` : null,
    state: g.nxplay_build ? 'nxplay' : 'none', app: false, progress: null, error: null, out_of_date: false, newer: 0,
    log: false, build_log: false, nxplay_save: g.nxplay_save, options: '',
  });
  const state = window.__state = {
    folder_name: app.library.name,
    games: app.games.map(fresh),
    art: Object.fromEntries(app.games.map(g => [g.id, g.art_data])),
    queue: [],  // ids waiting, in order
    setup_ready: true,  // Build Setup finds everything ready, as on the maintainer's Mac
    update: null,  // the App update toast (updater.Updater.toast), or null
    install_fails: false,  // Install Build Tools can't install
    copy_fails: false,  // Copy Log can't copy
    calls: [],
    logs: {},  // game id → { key, lines }: each game's last Build log
    ui: { log_open: true, log_width: 380, log_game: null },
  };
  const game = id => state.games.find(g => g.id === id);
  const at = i => state.games[i] || {};

  // A Mac app build's stages, by nxnative's names, how many seconds each takes here, and what
  // its progress counts (null: a stage whose work nxnative can't count).
  const STAGES = [
    ['survey: build nx_extract', 2, 3, 'steps'], ['survey: RomFS manifest', 2, 18342, 'files'], ['survey: code modules', 2, 3, 'modules'],
    ['extract base', 4, 26274, 'MB'], ['extract update', 2, 412, 'MB'], ['rewrite', 4, null], ['Mac host', 3, 812, 'files'],
    ['shader split', 3, 7, 'archives'], ['Mac shaders', 8, 42850, 'shaders'], ['Pipeline archive', 4, 33190, 'pipelines'], ['Mac app', 3, 5, 'steps'],
  ];
  const SUMMARIES = ['nx_extract built', '18,342 RomFS headers', 'main, subsdk0, sdk', '26.3 GB in extract/v131072',
    '412 MB patched', 'no rewriter patches', 'host built', '42,850 shaders in 7 archives', '42,850 translated, 0 failed',
    '33,190 pipelines', 'The Legend of Zelda - Ocarina of Time.app'];

  // Each game's Build log, as Switch Player writes it: preparation, the command, then the
  // build script's stages and messages.
  function newLog(g) {
    const key = `${g.id}:${(state.logs[g.id]?.n || 0) + 1}`;
    state.logs[g.id] = { key, n: (state.logs[g.id]?.n || 0) + 1, lines: [`Build of ${g.name} (/Users/you/Switch Games/${g.id}), 2026-11-06 21:40:12`] };
    g.build_log = true;
  }
  const say = (g, ...lines) => state.logs[g.id].lines.push(...lines);
  const PREP = {
    'Downloading nxnative': ["   Cloning into '/Users/you/Library/Application Support/Switch Player/nxnative.partial'...",
      '   remote: Enumerating objects: 81234, done.', '   remote: Counting objects: 100% (5120/5120), done.',
      '   remote: Compressing objects: 100% (2210/2210), done.', '   Receiving objects: 100% (81234/81234), 41.20 MiB | 9.80 MiB/s, done.',
      '   Resolving deltas: 100% (52011/52011), done.', '   Updating files: 100% (6113/6113), done.'],
    'Updating nxnative': ['   From https://github.com/seedds/nxnative', '      b2d46b8..c41e09a  main       -> origin/main',
      '   HEAD is now at c41e09a Ocarina of Time: the far grass matches console video'],
    'Installing Python packages': ['   Using CPython 3.13.9', '   Creating virtual environment at: env', '   Resolved 9 packages in 1.18s',
      '   Prepared 9 packages in 6.02s', '   Installed 9 packages in 41ms', '    + backports-zstd==1.0.0', '    + capstone==5.0.6',
      '    + fonttools==4.60.1', '    + lz4==4.4.4', '    + nsz==4.6.1'],
  };
  function command(g) {
    say(g, '', `$ /Users/you/Library/Application Support/Switch Player/nxnative-python/env/bin/python3 scripts/build_ipa_from_nsp.py /Users/you/Switch Games/${g.id} --platform mac-app --progress json`,
      '  in /Users/you/Library/Application Support/Switch Player/nxnative', '',
      'Mac app: the Mac shaders and the Mac host, packed into a Mac app; no iOS shaders, Xcode project or IPA',
      `work folder (extracted game files in extract/, kept for the next build): /Users/you/Switch Games/${g.id}/0400486024DEC000_build`);
  }
  function stageLines(g, k) {
    say(g, `── ${k}/${STAGES.length} ${STAGES[k - 1][0]}`);
    if (STAGES[k - 1][0] === 'Mac shaders') say(g, '   translating', '   shaders to rebuild (chain changed): 42850');
  }
  const stageEnd = (g, k) => say(g, `   done: ${SUMMARIES[k - 1]} (${STAGES[k - 1][1] * 60} s)`);

  function startStage(g, k, frac = 0) {
    const [name, secs, total, unit] = STAGES[k - 1];
    if (k > 1 && g.progress && !g.progress.preparing) stageEnd(g, k - 1);
    stageLines(g, k);
    g.progress = { k, n: STAGES.length, name, step: name === 'Mac shaders' ? 'translating' : '', frac: total ? frac : null, eta: null,
                   done: total ? Math.round(frac * total) : null, total, unit: unit || null, secs, at: frac * secs };
  }
  function prepare(g, steps) {
    const [first, ...rest] = steps;
    g.progress = { preparing: first, elapsed: 0, secs: 2, rest };
    say(g, `── ${first}`);
  }
  function tick() {
    if (state.still) return;  // scenarios for screenshots hold their Build still
    const g = state.games.find(x => x.state === 'building');
    if (!g || !g.progress) return;  // starting; its first stage comes in a moment
    const p = g.progress;
    if (p.preparing) {
      p.elapsed += 0.5;
      if (Math.floor(p.elapsed) === 1 && !p.said) { p.said = true; say(g, ...PREP[p.preparing]); }
      if (p.elapsed < p.secs) return;
      if (p.rest.length) prepare(g, p.rest);
      else { command(g); startStage(g, 1); }
      return;
    }
    p.at += 0.5;
    if (p.at >= p.secs) {
      if (p.k < p.n) startStage(g, p.k + 1);
      else finish(g);
      return;
    }
    if (p.total) { p.frac = Math.min(0.99, p.at / p.secs); p.done = Math.round(p.frac * p.total); p.eta = Math.round((p.secs - p.at) * 60); }
  }
  function finish(g) {
    stageEnd(g, STAGES.length);
    say(g, 'done', '', `Exit code 0. Each tool's full output is in /Users/you/Switch Games/${g.id}/<title ID>_build/logs/.`);
    Object.assign(g, { state: 'ready', app: true, progress: null, error: null, out_of_date: false, newer: 0 });
    next();
  }
  let downloaded = false;  // the first Build of the session downloads nxnative, as a first Build does
  function next() {
    if (state.games.some(x => x.state === 'building')) return;
    const id = state.queue.shift();
    if (!id) return;
    const g = game(id);
    g.state = 'building'; g.progress = null; newLog(g);
    setTimeout(() => {
      if (g.state !== 'building') return;
      prepare(g, downloaded ? ['Updating nxnative'] : ['Downloading nxnative', 'Installing Python packages']);
      downloaded = true;
    }, 400);
  }
  setInterval(tick, 500);

  // Ocarina of Time's Build up to stage k (its stage k running), for the held-still scenarios.
  function ocarinaUpTo(g, k) {
    newLog(g);
    for (const step of ['Downloading nxnative', 'Installing Python packages']) say(g, `── ${step}`, ...PREP[step]);
    command(g);
    say(g, 'no base game in this NUP: its update is taken as the whole game (if the game then stops on missing files, the update holds only what changed: pack a NUP with the base game)');
    for (let i = 1; i < k; i++) { stageLines(g, i); stageEnd(g, i); }
    startStage(g, k);
  }

  // The App update toast, as the updater gives it: a new id for each new step or result. A
  // result that fades is gone TOAST_KEEP seconds after it began.
  const TOAST_KEEP = 15000;
  let toastIds = 0, updateTimers = [];
  function setUpdate(kind, extra = {}) {
    const same = state.update && state.update.kind === kind;
    const fades = ['up_to_date', 'updated', 'off'].includes(kind) || (kind === 'failed' && !extra.sticky);
    state.update = { id: same ? state.update.id : ++toastIds, kind, version: null, progress: null, reason: null,
                     stage: null, sticky: false, fades, at: same ? state.update.at : Date.now(), ...extra };
  }
  const later = (ms, f) => updateTimers.push(setTimeout(f, ms));
  function runUpdate(found) {
    setUpdate('checking');
    if (!found) { later(1500, () => setUpdate('up_to_date')); return; }
    later(1500, () => {
      setUpdate('downloading', { version: '0.1.4', progress: 0 });
      for (let i = 1; i <= 16; i++) later(i * 250, () => setUpdate('downloading', { version: '0.1.4', progress: i / 16 }));
      later(4300, () => setUpdate('verifying', { version: '0.1.4' }));
      later(6000, () => setUpdate('ready', { version: '0.1.4' }));
    });
  }
  function installUpdate() {
    if (state.update?.kind !== 'ready') return;
    setUpdate('installing', { version: '0.1.4' });
    later(1500, () => { console.log('Preview: would quit, swap in 0.1.4 and reopen'); setUpdate('updated', { version: '0.1.4' }); });
  }

  const named = name => state.games.find(g => g.name.includes(name)) || {};
  const scenarios = {
    preparing: () => {
      state.still = true;
      const g = named('Ocarina');
      Object.assign(g, { state: 'building' }); newLog(g);
      say(g, '── Downloading nxnative', ...PREP['Downloading nxnative'].slice(0, 4));
      g.progress = { preparing: 'Downloading nxnative', elapsed: 25 };
      Object.assign(named('Rhythm'), { state: 'ready', app: true, log: true });
    },
    ocarina: () => {
      state.still = true;
      const g = named('Ocarina');
      g.state = 'building'; ocarinaUpTo(g, 9);
      Object.assign(g.progress, { frac: 0.42, done: 18204, eta: 3900 });
      say(g, '   2,312 shaders with an attribute offset the translator drops (shading rate): translated without it');
      Object.assign(named('Echoes'), { state: 'waiting' }); state.queue = [named('Echoes').id];
      Object.assign(named('Rhythm'), { state: 'ready', app: true, log: true, build_log: true });
    },
    closed: () => { state.ui.log_open = false; },
    'copy-fails': () => { state.copy_fails = true; },
    building: () => {
      Object.assign(at(0), { state: 'building' }); newLog(at(0)); startStage(at(0), 9, 0.42); at(0).progress.at = 0.42 * 8; at(0).progress.eta = 280;
      Object.assign(at(1), { state: 'waiting' }); state.queue = [at(1).id].filter(Boolean);
      Object.assign(at(2), { state: 'ready', app: true, log: true });
    },
    // A Build still preparing, with what it's doing: progress.preparing, then the script's own stages.
    installing: () => {
      state.still = true;
      const g = named('Ocarina');
      Object.assign(g, { state: 'building' }); newLog(g);
      say(g, '── Downloading nxnative', ...PREP['Downloading nxnative'], '── Installing Python packages', ...PREP['Installing Python packages'].slice(0, 3));
      g.progress = { preparing: 'Installing Python packages', elapsed: 12 };
    },
    'setup-missing': () => { state.setup_ready = false; },
    'setup-ready': () => { state.setup_ready = true; },  // the default, kept for older links
    'update-checking': () => setUpdate('checking'),
    'update-downloading': () => setUpdate('downloading', { version: '0.1.4', progress: 0.42 }),
    'update-verifying': () => setUpdate('verifying', { version: '0.1.4' }),
    'update-ready': () => setUpdate('ready', { version: '0.1.4' }),
    'update-held': () => { setUpdate('ready', { version: '0.1.4' }); state.held = true; },
    'update-installing': () => setUpdate('installing', { version: '0.1.4' }),
    'update-uptodate': () => setUpdate('up_to_date'),
    'update-updated': () => setUpdate('updated', { version: '0.1.4' }),
    'update-off': () => setUpdate('off', { reason: 'move Switch Player to your Applications folder' }),
    'update-failed': () => setUpdate('failed', { stage: 'check', sticky: true,
      reason: "Couldn't reach GitHub (curl: (6) Could not resolve host: api.github.com)." }),
    'update-failed-auto': () => setUpdate('failed', { stage: 'check',
      reason: "Couldn't reach GitHub (curl: (6) Could not resolve host: api.github.com)." }),
    'update-run': () => runUpdate(true),
    'update-none': () => runUpdate(false),
    'install-fails': () => { state.install_fails = true; state.setup_ready = false; },
    ready: () => {
      state.games.forEach(g => Object.assign(g, { state: 'ready', app: true, log: true }));
      at(0).out_of_date = true;
      at(1).newer = 142;
    },
    failed: () => {
      const g = named('Ocarina');
      newLog(g);
      say(g, '── Updating nxnative', ...PREP['Updating nxnative']);
      command(g);
      for (let i = 1; i < 9; i++) { stageLines(g, i); stageEnd(g, i); }
      stageLines(g, 9);
      // as nxnative prints an error: through stderr with the time, and as a say event without it
      say(g, '[21:53:49] [RED] v131072: 3 file(s) whose bytes do not start as their names promise (a .zs file that is not a zstd stream), so the game files are damaged:',
        '  Tex/CmnTex_Map_Soil_FairyFountain_Floor_D_Nrm.bntx.zs (216,015 bytes, starts 00000000)',
        'Pack again from a good copy of the game (--allow-damaged packs these files as they are)',
        '[RED] less than 31 GB free on the volume of /Users/you/Switch Games/Ocarina of Time/0400486024DEC000_build',
        '   failed (12 s)', '', "Exit code 1. Each tool's full output is in /Users/you/Switch Games/Ocarina of Time/<title ID>_build/logs/.");
      Object.assign(g, { state: 'failed', error: 'less than 31 GB free on the volume of /Users/you/Switch Games/Ocarina of Time/0400486024DEC000_build' });
      state.ui.log_game = g.id;
      Object.assign(at(0), {
        state: 'failed', error: 'the game stopped after 41 s in the Mac host (exit 134): its shaders are incomplete '
          + 'and the IPA will not get further; ---- last lines of logs/mac_host_run.log ----\n'
          + '[nvn] unhandled texture format 0x2b in a sampler descriptor\nAbort trap: 6',
      });
      Object.assign(named('Rhythm'), { state: 'stopped' });
      Object.assign(named('Jamboree'), { state: 'failed', app: true, log: true, error: "captured shaders the profile's shader table does not list: 3" });
    },
    // The README's screenshots: one of each state a player meets first, held still.
    readme: () => {
      state.still = true;
      state.ui.log_open = false;  // as the README's screenshots were taken
      Object.assign(named('Echoes'), { state: 'building' }); newLog(named('Echoes')); startStage(named('Echoes'), 9, 0.42);
      named('Echoes').progress && Object.assign(named('Echoes').progress, { at: 0.42 * 8, eta: 280 });
      Object.assign(named('Rhythm'), { state: 'ready', app: true, log: true, newer: 12 });
      Object.assign(named('Jamboree'), { state: 'nxplay' });
    },
    noart: () => state.games.forEach(g => { g.art = null; }),
    many: () => {
      const base = state.games.slice();
      for (let n = 2; state.games.length < 11 && base.length; n++) {
        for (const g of base) {
          if (state.games.length >= 11) break;
          const copy = { ...g, id: `${g.id} ${n}`, name: `${g.name} ${n}` };
          state.art[copy.id] = state.art[g.id];
          state.games.push(copy);
        }
      }
    },
    empty: () => { state.games = []; },
    nofolder: () => { state.folder_name = null; state.games = []; },
    dark: () => { document.documentElement.dataset.theme = 'dark'; },
    light: () => { document.documentElement.dataset.theme = 'light'; },
  };

  const handlers = {
    library: () => ({
      folder_name: state.folder_name,
      // a plain copy, as the bridge sends, without the simulation's own fields
      games: state.games.map(g => ({ ...g, progress: g.progress && (({ secs, at, rest, said, ...p }) => p)(g.progress) })),
      update: (() => {
        const u = state.update;
        if (!u || u.id === state.dismissed || (u.fades && !state.still && Date.now() - u.at > TOAST_KEEP)) return null;
        const { at, ...shown } = u;
        // held: a Build is running or waiting, or the update-held scenario says so
        return u.kind === 'ready' ? { ...shown, held: !!state.held || state.games.some(g => g.state === 'building' || g.state === 'waiting') } : shown;
      })(),
    }),
    // A piece of the game's Build log from `offset` (here a line count), all of it again when
    // the log is another one than `key` names.
    build_log: (id, offset, key) => {
      const log = state.logs[id];
      if (!log) return null;
      const from = key === log.key ? offset : 0;
      return { key: log.key, offset: log.lines.length, text: log.lines.slice(from).map(l => `${l}\n`).join('') };
    },
    open_build_log: id => console.log(`Preview: would open ${game(id).name}'s Build log in Console`),
    copy_build_log: id => {
      const log = state.logs[id];
      if (!log || state.copy_fails) return false;
      console.log(`Preview: would copy ${game(id).name}'s whole Build log (${log.lines.length} lines) to the clipboard`);
      return true;
    },
    ui_state: () => ({ ...state.ui }),
    set_ui_state: values => { Object.assign(state.ui, values); },
    art: id => state.art[id] || null,
    choose_library: () => { state.folder_name = app.library.name; state.games = app.games.map(fresh); },
    play: id => console.log(`Preview: would open ${game(id).name}.app with --no-launcher`),
    open_settings: id => console.log(`Preview: would open ${game(id).name}.app with its Settings window`),
    build: id => {
      const g = game(id);
      if (g.state === 'building' || g.state === 'waiting') return;
      g.state = 'waiting'; g.error = null; state.queue.push(id);
      next();
    },
    stop: id => {
      const g = game(id);
      if (g.state === 'waiting') {
        state.queue = state.queue.filter(x => x !== id);
        g.state = g.app ? 'ready' : fresh(app.games.find(x => x.id === id) || {}).state;
      } else if (g.state === 'building') {
        say(g, '', 'Stopped');
        Object.assign(g, { state: 'stopped', progress: null });
        next();
      }
    },
    set_build_options: (id, text) => { game(id).options = text; },
    setup: () => {
      const ok = state.setup_ready;
      return {
        ready: ok,
        items: [
          { id: 'mac', name: 'Apple silicon and macOS', ready: true, detail: '26.6', action: null },
          { id: 'xcode', name: 'Xcode', ready: true, detail: 'Xcode is selected and its first launch is complete.', action: null },
          { id: 'metal', name: 'Metal Toolchain', ready: ok,
            detail: ok ? 'Apple metal version 4.0' : 'In Terminal, run: xcodebuild -downloadComponent MetalToolchain', action: null },
          { id: 'dotnet', name: '.NET SDK', ready: ok,
            detail: ok ? '.NET SDK 10.0.403 is installed.' : 'In Terminal, run: brew install --cask dotnet-sdk (it asks for your password)',
            action: null },
          { id: 'tools', name: 'Build tools', ready: ok,
            detail: ok ? 'xcodegen, zstd and glslang are installed.' : 'Homebrew tools are missing.',
            action: ok ? null : 'install' },
        ],
      };
    },
    install_update: () => installUpdate(),
    dismiss_update: id => { state.dismissed = id; },
    open_releases_page: () => console.log('Preview: would open https://github.com/DigitalN/SwitchPlayer/releases'),
    import_nxplay_save: id => { console.log(`Preview: would ask, then import ${game(id).name}'s NX Play save`); return true; },
    install_tools: () => {
      console.log(`Preview: would install the Brewfile tools${state.install_fails ? ', and fail' : ''}`);
      return !state.install_fails;
    },
    log_error: message => console.error('Page error:', message),
  };

  window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
    state.calls.push([name, ...args]);
    return handlers[name] ? handlers[name](...args) ?? null : null;
  } }) };

  window.__preview = {
    start() {
      const q = new URLSearchParams(location.search);
      for (const s of (q.get('s') || '').split(',').filter(Boolean)) {
        if (scenarios[s]) scenarios[s]();
        else console.error(`Preview: no scenario "${s}". Scenarios: ${Object.keys(scenarios).join(', ')}`);
      }
      setTimeout(() => window.dispatchEvent(new Event('pywebviewready')), 0);
    },
  };
})();
