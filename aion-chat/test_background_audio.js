const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const read = file => fs.readFileSync(path.join(__dirname, 'static', file), 'utf8');

test('monitor cues bypass speech playback while the App WebView is hidden', () => {
  for (const file of ['chat.js', 'common.js', '../../AionApp/app/src/main/assets/static/common.js']) {
    const source = read(file);
    // Exercise each cue's actual playback block, without booting the whole chat UI.
    const blocks = [...source.matchAll(/(?:const audio = new Audio\('\/public\/AionMonitoralart\.mp3'\);\s*audio\.play\(\)\.catch\(\(\) => \{\}\);|if \(window\.AionTtsAudio[\s\S]*?audio\.play\(\)\.catch\(\(\) => \{\}\);\s*\})/g)];
    assert.equal(blocks.length, file === 'chat.js' ? 2 : 1);
    for (const [block] of blocks) {
      const played = [];
      const context = {
        window: { AionTtsAudio: {
          play: () => { throw new Error('monitor cue must not join the speech queue'); },
          playMonitorAlert: () => { played.push('/public/AionMonitoralart.mp3'); return true; },
        } },
        Audio: class { play() { throw new Error('hidden HTML audio cannot start'); } },
      };
      vm.runInNewContext(block, context);
      assert.deepEqual(played, ['/public/AionMonitoralart.mp3']);
      let fallbackUrl;
      vm.runInNewContext(block, {
        window: {},
        Audio: class { constructor(url) { fallbackUrl = url; } play() { return Promise.resolve(); } },
      });
      assert.equal(fallbackUrl, '/public/AionMonitoralart.mp3');
      // An older App has no immediate native entry point: use HTML audio,
      // never fall back to its queued bridge.play().
      fallbackUrl = null;
      vm.runInNewContext(block, {
        window: { AionTtsAudio: { play: context.window.AionTtsAudio.play } },
        Audio: class { constructor(url) { fallbackUrl = url; } play() { return Promise.resolve(); } },
      });
      assert.equal(fallbackUrl, '/public/AionMonitoralart.mp3');
    }
  }
});
