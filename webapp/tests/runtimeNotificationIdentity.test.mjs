import assert from 'node:assert/strict';
import path from 'node:path';
import {test} from 'node:test';
import {createNotificationIdentity} from '../packages/main/src/runtimeNotificationIdentity.ts';

const win = path.win32;
const applicationId = 'com.electron.aes';
const executablePath = 'D:\\Apps\\AutoEpicSeven\\aes.exe';
const appDataPath = 'C:\\Users\\Tester\\AppData\\Roaming';

function errorWithCode(code) {
  return Object.assign(new Error(code), {code});
}

function fixture(optionOverrides = {}) {
  const options = {
    platform: 'win32',
    isPackaged: true,
    executablePath,
    appDataPath,
    appId: applicationId,
    ...optionOverrides,
  };
  const entries = new Map([[appDataPath, {kind: 'directory'}]]);
  const calls = [];
  const warnings = [];
  const directories = [
    'Microsoft',
    'Windows',
    'Start Menu',
    'Programs',
    'AutoEpicSeven Notifications',
  ];
  let parent = appDataPath;
  const directoryPaths = directories.map(name => (parent = win.join(parent, name)));
  const shortcutPath = win.join(parent, `AutoEpicSeven (${applicationId}).lnk`);
  const matching = {target: executablePath, appUserModelId: applicationId};
  const deps = {
    path: win,
    lstat: candidate => {
      calls.push(['lstat', candidate]);
      const entry = entries.get(candidate);
      if (!entry) throw errorWithCode('ENOENT');
      return {
        isFile: () => entry.kind === 'file',
        isDirectory: () => entry.kind === 'directory',
        isSymbolicLink: () => entry.kind === 'symlink',
      };
    },
    mkdir: candidate => {
      calls.push(['mkdir', candidate]);
      if (entries.has(candidate)) throw errorWithCode('EEXIST');
      assert.equal(entries.get(win.dirname(candidate))?.kind, 'directory');
      entries.set(candidate, {kind: 'directory'});
    },
    reserveShortcut: candidate => {
      calls.push(['reserve', candidate]);
      if (entries.has(candidate)) throw errorWithCode('EEXIST');
      entries.set(candidate, {kind: 'file'});
    },
    readShortcut: candidate => {
      calls.push(['read', candidate]);
      const details = entries.get(candidate)?.details;
      if (!details) throw errorWithCode('INVALID_SHORTCUT');
      return details;
    },
    writeShortcut: (candidate, operation, details) => {
      calls.push(['write', candidate, operation, details]);
      const previous = entries.get(candidate);
      assert.equal(previous?.kind, 'file', 'Only a regular reserved or owned file may be written.');
      assert.equal(candidate, shortcutPath, 'Only the dedicated shortcut may be written.');
      if (operation === 'create') {
        assert.equal(
          previous.details,
          undefined,
          'Never replace a populated shortcut while creating.',
        );
      } else {
        assert.equal(operation, 'update');
        assert.equal(previous.details?.appUserModelId, applicationId);
        assert.equal(previous.details?.description, 'AutoEpicSeven system notifications');
      }
      entries.set(candidate, {kind: 'file', details});
      return true;
    },
  };
  function seedDirectories() {
    for (const candidate of directoryPaths) entries.set(candidate, {kind: 'directory'});
  }
  function seedShortcut(details = matching, kind = 'file') {
    seedDirectories();
    entries.set(shortcutPath, {kind, details});
  }
  const make = () => createNotificationIdentity(options, deps, message => warnings.push(message));
  const mutations = () => calls.filter(([kind]) => ['mkdir', 'reserve', 'write'].includes(kind));
  return {
    options,
    entries,
    calls,
    warnings,
    deps,
    directoryPaths,
    shortcutPath,
    matching,
    seedDirectories,
    seedShortcut,
    make,
    mutations,
  };
}

test('construction and capability-independent setup have no filesystem side effects', () => {
  const f = fixture();
  f.make();
  assert.deepEqual(f.calls, []);
  assert.deepEqual(f.warnings, []);
});

test('development and non-Windows applications do not inspect or create shortcuts', () => {
  for (const options of [{isPackaged: false}, {platform: 'linux'}, {platform: 'darwin'}]) {
    const f = fixture(options);
    assert.equal(f.make().ensureReady(), true);
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.warnings, []);
  }
});

test('creates a dedicated shortcut only after validating each parent and reserving the file', () => {
  const f = fixture();
  const identity = f.make();
  assert.equal(identity.ensureReady(), true);
  assert.deepEqual(
    f.calls.filter(([kind]) => kind === 'mkdir').map(([, candidate]) => candidate),
    f.directoryPaths,
  );
  const reserveIndex = f.calls.findIndex(([kind]) => kind === 'reserve');
  const writeIndex = f.calls.findIndex(([kind]) => kind === 'write');
  assert.ok(reserveIndex < writeIndex);
  assert.deepEqual(f.calls[writeIndex], [
    'write',
    f.shortcutPath,
    'create',
    {
      target: executablePath,
      cwd: win.dirname(executablePath),
      appUserModelId: applicationId,
      description: 'AutoEpicSeven system notifications',
      icon: executablePath,
      iconIndex: 0,
    },
  ]);
  const callsAfterSuccess = f.calls.length;
  assert.equal(identity.ensureReady(), true);
  assert.equal(
    f.calls.length,
    callsAfterSuccess,
    'Later notifications reuse the validated identity.',
  );
  assert.deepEqual(f.warnings, []);
});

test('reuses a matching shortcut with Windows-normalized target casing and separators', () => {
  const f = fixture();
  f.seedShortcut({target: 'd:/apps/AutoEpicSeven/./AES.exe', appUserModelId: applicationId});
  assert.equal(f.make().ensureReady(), true);
  assert.deepEqual(f.mutations(), []);
  assert.deepEqual(f.warnings, []);
});

for (const [label, details] of [
  ['another target', {target: 'D:\\Other\\other.exe', appUserModelId: applicationId}],
  ['another application ID', {target: executablePath, appUserModelId: 'com.other.application'}],
  ['a missing application ID', {target: executablePath}],
  ['a relative target', {target: 'aes.exe', appUserModelId: applicationId}],
]) {
  test(`refuses ${label} without replacing the existing shortcut`, () => {
    const f = fixture();
    f.seedShortcut(details);
    assert.equal(f.make().ensureReady(), false);
    assert.deepEqual(f.mutations(), []);
    assert.strictEqual(f.entries.get(f.shortcutPath).details, details);
    assert.match(f.warnings[0], /conflict/);
  });
}

for (const kind of ['directory', 'symlink']) {
  test(`refuses a ${kind} at the dedicated shortcut location`, () => {
    const f = fixture();
    f.seedShortcut(undefined, kind);
    assert.equal(f.make().ensureReady(), false);
    assert.deepEqual(f.mutations(), []);
    assert.equal(
      f.calls.some(([operation]) => operation === 'read'),
      false,
    );
  });
}

test('keeps an unreadable shortcut instead of treating it as absent', () => {
  const f = fixture();
  f.seedShortcut();
  f.deps.readShortcut = () => {
    throw errorWithCode('INVALID_SHORTCUT');
  };
  assert.equal(f.make().ensureReady(), false);
  assert.deepEqual(f.mutations(), []);
  assert.match(f.warnings[0], /INVALID_SHORTCUT/);
});

for (const kind of ['file', 'symlink']) {
  test(`rejects an unsafe ${kind} in the Start Menu directory chain`, () => {
    const f = fixture();
    f.entries.set(f.directoryPaths[0], {kind});
    assert.equal(f.make().ensureReady(), false);
    assert.deepEqual(f.mutations(), []);
    assert.match(f.warnings[0], /unsafe/);
  });
}

test('does not invent a missing application-data root', () => {
  const f = fixture();
  f.entries.clear();
  assert.equal(f.make().ensureReady(), false);
  assert.deepEqual(f.mutations(), []);
});

for (const options of [
  {appDataPath: 'relative-app-data'},
  {executablePath: 'relative-app.exe'},
  {appId: '../other'},
  {appId: 'com.other:stream'},
]) {
  test(`invalid identity options cannot create files: ${JSON.stringify(options)}`, () => {
    const f = fixture(options);
    assert.equal(f.make().ensureReady(), false);
    assert.deepEqual(f.calls, []);
  });
}

test('permission errors are preserved and are never treated as missing directories', () => {
  const f = fixture();
  f.deps.lstat = () => {
    throw errorWithCode('EACCES');
  };
  assert.equal(f.make().ensureReady(), false);
  assert.deepEqual(f.mutations(), []);
  assert.match(f.warnings[0], /EACCES/);
});

test('a concurrent directory creator is accepted only after inspecting its result', () => {
  const f = fixture();
  const originalMkdir = f.deps.mkdir;
  f.deps.mkdir = candidate => {
    originalMkdir(candidate);
    throw errorWithCode('EEXIST');
  };
  assert.equal(f.make().ensureReady(), true);
  assert.deepEqual(f.warnings, []);
});

test('a concurrently inserted directory symlink is rejected before writing a shortcut', () => {
  const f = fixture();
  f.deps.mkdir = candidate => {
    f.entries.set(candidate, {kind: 'symlink'});
    throw errorWithCode('EEXIST');
  };
  assert.equal(f.make().ensureReady(), false);
  assert.equal(
    f.calls.some(([kind]) => kind === 'reserve' || kind === 'write'),
    false,
  );
});

test('a shortcut appearing before exclusive reservation is never overwritten', () => {
  const f = fixture();
  f.seedDirectories();
  const unrelated = {target: 'D:\\Other\\other.exe', appUserModelId: applicationId};
  f.deps.reserveShortcut = candidate => {
    f.entries.set(candidate, {kind: 'file', details: unrelated});
    throw errorWithCode('EEXIST');
  };
  assert.equal(f.make().ensureReady(), false);
  assert.equal(
    f.calls.some(([kind]) => kind === 'write'),
    false,
  );
  assert.strictEqual(f.entries.get(f.shortcutPath).details, unrelated);
  assert.match(f.warnings[0], /conflict/);
});

test('a matching shortcut appearing during reservation can be safely reused', () => {
  const f = fixture();
  f.seedDirectories();
  f.deps.reserveShortcut = candidate => {
    f.entries.set(candidate, {kind: 'file', details: f.matching});
    throw errorWithCode('EEXIST');
  };
  assert.equal(f.make().ensureReady(), true);
  assert.equal(
    f.calls.some(([kind]) => kind === 'write'),
    false,
  );
});

for (const failure of ['false', 'throw']) {
  test(`a ${failure} write failure retains the reserved file and does not repeatedly retry`, () => {
    const f = fixture();
    let attempts = 0;
    f.deps.writeShortcut = () => {
      attempts += 1;
      if (failure === 'throw') throw errorWithCode('EACCES');
      return false;
    };
    const identity = f.make();
    assert.equal(identity.ensureReady(), false);
    assert.equal(identity.ensureReady(), false);
    assert.equal(attempts, 1);
    assert.deepEqual(f.entries.get(f.shortcutPath), {kind: 'file'});
    assert.equal(f.warnings.length, 1);
  });
}

test('reports failure when the written shortcut does not contain the requested identity', () => {
  const f = fixture();
  f.deps.writeShortcut = candidate => {
    f.entries.set(candidate, {kind: 'file', details: {target: executablePath}});
    return true;
  };
  assert.equal(f.make().ensureReady(), false);
  assert.match(f.warnings[0], /conflict/);
});

test('updates the owned notification shortcut after moving the portable application', () => {
  const f = fixture();
  f.seedShortcut({
    target: 'D:\\OldInstall\\aes.exe',
    cwd: 'D:\\OldInstall',
    icon: 'D:\\OldInstall\\aes.exe',
    args: '--obsolete-startup-argument',
    appUserModelId: applicationId,
    description: 'AutoEpicSeven system notifications',
  });
  const identity = f.make();
  assert.equal(identity.ensureReady(), true);
  const expected = {
    target: executablePath,
    cwd: win.dirname(executablePath),
    args: '',
    appUserModelId: applicationId,
    description: 'AutoEpicSeven system notifications',
    icon: executablePath,
    iconIndex: 0,
  };
  assert.deepEqual(f.mutations(), [['write', f.shortcutPath, 'update', expected]]);
  assert.deepEqual(f.entries.get(f.shortcutPath).details, expected);
  assert.equal(f.calls.filter(([kind]) => kind === 'read').length, 2);
  assert.equal(identity.ensureReady(), true);
  assert.equal(f.mutations().length, 1);
  assert.deepEqual(f.warnings, []);
});

for (const description of [undefined, 'An unrelated application shortcut']) {
  test(`does not relocate an unowned shortcut with description ${String(description)}`, () => {
    const f = fixture();
    const existing = {
      target: 'D:\\OtherInstall\\aes.exe',
      appUserModelId: applicationId,
      ...(description === undefined ? {} : {description}),
    };
    f.seedShortcut(existing);
    assert.equal(f.make().ensureReady(), false);
    assert.deepEqual(f.mutations(), []);
    assert.strictEqual(f.entries.get(f.shortcutPath).details, existing);
    assert.match(f.warnings[0], /conflict/);
  });
}

test('fails relocation when reading the updated shortcut does not confirm the new identity', () => {
  const f = fixture();
  f.seedShortcut({
    target: 'D:\\OldInstall\\aes.exe',
    appUserModelId: applicationId,
    description: 'AutoEpicSeven system notifications',
  });
  const originalWrite = f.deps.writeShortcut;
  f.deps.writeShortcut = (candidate, operation, details) => {
    assert.equal(operation, 'update');
    originalWrite(candidate, operation, details);
    f.entries.set(candidate, {
      kind: 'file',
      details: {...details, appUserModelId: 'com.other.application'},
    });
    return true;
  };
  assert.equal(f.make().ensureReady(), false);
  assert.equal(f.mutations().length, 1);
  assert.equal(f.calls.filter(([kind]) => kind === 'read').length, 2);
  assert.match(f.warnings[0], /conflict/);
  assert.equal(f.entries.get(f.shortcutPath).kind, 'file');
});
