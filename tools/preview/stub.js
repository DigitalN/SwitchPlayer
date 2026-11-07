// Stand-in for the app's Api (the pywebview bridge), so index.html runs in a browser (see
// serve.py). The games are the preview Library folder's, named and with art as the app finds
// them; their states are made up, and Build, Stop and the queue are simulated, a stage's
// second standing for a minute.
//
// Query options, combined with commas: ?s=building,dark
//   library: (default: as it is now, nothing built yet) building preparing installing ready failed
//            noart many empty nofolder readme
//   setup:   setup-ready (Build Setup with everything ready, where Not Now becomes Done)
//            install-fails (Install Build Tools can't install)
//   theme:   dark light
// Change window.__state while it runs to try anything else; the next poll picks it up.
// window.__state.calls is every API call the page made. Page errors that the app would log
// go to the console.
(() => {
  const app = window.__app;
  const fresh = g => ({
    id: g.id, name: g.name, title_id: g.title_id, art: g.art_data ? `${g.title_id}:1` : null,
    state: g.nxplay_build ? 'nxplay' : 'none', app: false, progress: null, error: null, out_of_date: false, newer: 0,
    log: false, nxplay_save: g.nxplay_save, options: '',
  });
  const state = window.__state = {
    folder_name: app.library.name,
    games: app.games.map(fresh),
    art: Object.fromEntries(app.games.map(g => [g.id, g.art_data])),
    queue: [],  // ids waiting, in order
    setup_ready: false,  // Build Setup finds everything ready
    install_fails: false,  // Install Build Tools can't install
    calls: [],
  };
  const game = id => state.games.find(g => g.id === id);
  const at = i => state.games[i] || {};

  // A Mac app build's stages, by nxnative's names, and how many seconds each takes here.
  // A null frac is a stage whose work nxnative can't count.
  const STAGES = [
    ['survey: build nx_extract', 2], ['survey: RomFS manifest', 2], ['survey: code modules', 2],
    ['extract base', 4], ['extract update', 2], ['rewrite', 4, 'uncounted'], ['Mac host', 3],
    ['shader split', 3], ['Mac shaders', 8], ['Pipeline archive', 4], ['Mac app', 3],
  ];
  function startStage(g, k, frac = 0) {
    const [name, secs, uncounted] = STAGES[k - 1];
    g.progress = { k, n: STAGES.length, name, step: '', frac: uncounted ? null : frac, eta: null, secs, done: frac * secs, uncounted };
  }
  function tick() {
    if (state.still) return;  // the readme scenario holds its Build still for screenshots
    const g = state.games.find(x => x.state === 'building');
    if (!g || !g.progress) return;  // starting; its first stage comes in a moment
    const p = g.progress;
    if (p.preparing) return;  // the Engine update and the Python packages install take as long as they take
    p.done += 0.5;
    if (p.done >= p.secs) {
      if (p.k < p.n) startStage(g, p.k + 1);
      else finish(g);
      return;
    }
    if (!p.uncounted) { p.frac = Math.min(0.99, p.done / p.secs); p.eta = Math.round((p.secs - p.done) * 60); }
  }
  function finish(g) {
    Object.assign(g, { state: 'ready', app: true, progress: null, error: null, out_of_date: false, newer: 0 });
    next();
  }
  function next() {
    if (state.games.some(x => x.state === 'building')) return;
    const id = state.queue.shift();
    if (id) { const g = game(id); g.state = 'building'; g.progress = null; setTimeout(() => g.state === 'building' && startStage(g, 1), 600); }
  }
  setInterval(tick, 500);

  function preparing(what) {
    Object.assign(at(0), { state: 'building', progress: { preparing: what } });
    Object.assign(at(1), { state: 'waiting' }); state.queue = [at(1).id].filter(Boolean);
    Object.assign(at(2), { state: 'ready', app: true, log: true });
  }

  const scenarios = {
    building: () => {
      Object.assign(at(0), { state: 'building' }); startStage(at(0), 9, 0.42); at(0).progress.done = 0.42 * 8; at(0).progress.eta = 280;
      Object.assign(at(1), { state: 'waiting' }); state.queue = [at(1).id].filter(Boolean);
      Object.assign(at(2), { state: 'ready', app: true, log: true });
    },
    // A Build still preparing, with what it's doing: progress.preparing, then the script's own stages.
    preparing: () => preparing('Updating nxnative'),
    installing: () => preparing('Installing Python packages'),
    'setup-ready': () => { state.setup_ready = true; },
    'install-fails': () => { state.install_fails = true; },
    ready: () => {
      state.games.forEach(g => Object.assign(g, { state: 'ready', app: true, log: true }));
      at(0).out_of_date = true;
      at(1).newer = 142;
    },
    failed: () => {
      Object.assign(at(0), {
        state: 'failed', error: 'the game stopped after 41 s in the Mac host (exit 134): its shaders are incomplete '
          + 'and the IPA will not get further; ---- last lines of logs/mac_host_run.log ----\n'
          + '[nvn] unhandled texture format 0x2b in a sampler descriptor\nAbort trap: 6',
      });
      Object.assign(at(1), { state: 'stopped' });
      Object.assign(at(2), { state: 'failed', app: true, log: true, error: "captured shaders the profile's shader table does not list: 3" });
    },
    // The README's screenshots: one of each state a player meets first, held still.
    readme: () => {
      state.still = true;
      const named = name => state.games.find(g => g.name.includes(name)) || {};
      Object.assign(named('Echoes'), { state: 'building' }); startStage(named('Echoes'), 9, 0.42);
      named('Echoes').progress && Object.assign(named('Echoes').progress, { done: 0.42 * 8, eta: 280 });
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
      games: state.games.map(g => ({ ...g, progress: g.progress && (({ secs, done, uncounted, ...p }) => p)(g.progress) })),
    }),
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
