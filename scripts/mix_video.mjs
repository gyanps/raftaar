// Frames + narration + music -> the final video. Run by scripts/demo_video.mjs, or alone after changing the sound:
//
//   node scripts/mix_video.mjs [out dir]        default dist/video
//
// Reads frames.txt and scenes.json (written by the recorder), vo/<scene>.wav with vo/durations.json
// (scripts/voiceover.py) and music.mp3.
//
// Sound: every narration clip gets one fixed gain so it sits at the same loudness (nothing changes inside a
// line); the music dips while the voice speaks and comes back up in the gaps; the finished mix is then set to
// -16 LUFS with its peaks kept under -1 dB. Picture: 2560 x 1440 master from the double-resolution frames.
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const OUT = process.argv[2] || 'dist/video';
const read = (name, dflt) => (existsSync(join(OUT, name)) ? JSON.parse(readFileSync(join(OUT, name), 'utf8')) : dflt);
const seconds = [...readFileSync(join(OUT, 'frames.txt'), 'utf8').matchAll(/^duration ([\d.]+)$/gm)].reduce((s, m) => s + +m[1], 0);
const scenes = read('scenes.json', {});
const VO = read('vo/durations.json', {});
const clipFile = (k) => ['wav', 'mp3'].map((e) => join(OUT, 'vo', k + '.' + e)).find(existsSync);
const clips = Object.keys(VO).filter((k) => scenes[k] != null && clipFile(k));
const music = existsSync(join(OUT, 'music.mp3'));
const ff = (args) => spawnSync('ffmpeg', ['-hide_banner', '-nostats', ...args], { encoding: 'utf8' });
const loudness = (file, extra = []) => {            // integrated loudness and true peak of a file
  const r = ff(['-i', file, ...extra, '-af', 'loudnorm=I=-16:TP=-1:LRA=11:print_format=json', '-vn', '-f', 'null', '-']);
  const j = JSON.parse(r.stderr.slice(r.stderr.lastIndexOf('{'), r.stderr.lastIndexOf('}') + 1));   // ffmpeg prints more after the JSON
  return { i: +j.input_i, tp: +j.input_tp };
};
const fail = (what, r) => { console.error(what + ' failed: ' + String(r.stderr).slice(-700)); process.exit(1); };

// ---- sound ---------------------------------------------------------------------------------------------
let audio = null;
if (music || clips.length) {
  const args = [];
  if (music) args.push('-stream_loop', '-1', '-i', join(OUT, 'music.mp3'));
  clips.forEach((k) => args.push('-i', clipFile(k)));
  const first = music ? 1 : 0;
  const parts = [];
  clips.forEach((k, i) => {
    const gain = (-17 - loudness(clipFile(k)).i).toFixed(2);          // one fixed gain per clip
    const ms = Math.round(scenes[k] * 1000 + 450);
    parts.push('[' + (i + first) + ':a]aresample=48000,aformat=channel_layouts=stereo,volume=' + gain + 'dB,adelay=' + ms + '|' + ms + '[v' + i + ']');
  });
  if (clips.length) parts.push(clips.map((_, i) => '[v' + i + ']').join('') + 'amix=inputs=' + clips.length + ':normalize=0:duration=longest,apad[vox]');
  if (music && clips.length) {
    parts.push('[0:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.30,afade=t=in:d=2,afade=t=out:st=' + (seconds - 3).toFixed(1) + ':d=3[bed]');
    parts.push('[vox]asplit=2[key][say]');
    parts.push('[bed][key]sidechaincompress=threshold=0.012:ratio=9:attack=25:release=900[duck]');
    parts.push('[duck][say]amix=inputs=2:normalize=0:duration=first[a]');
  } else if (music) parts.push('[0:a]aresample=48000,volume=0.35,afade=t=in:d=2,afade=t=out:st=' + (seconds - 3).toFixed(1) + ':d=3[a]');
  else parts.push('[vox]anull[a]');
  audio = join(OUT, 'mix.wav');
  const r = ff(['-y', ...args, '-filter_complex', parts.join(';'), '-map', '[a]', '-t', seconds.toFixed(2), '-c:a', 'pcm_s24le', audio]);
  if (r.status) fail('sound mix', r);
}

// ---- picture + sound -------------------------------------------------------------------------------------
const args = ['-y', '-f', 'concat', '-safe', '0', '-i', join(OUT, 'frames.txt')];
let level = '';
if (audio) {
  const m = loudness(audio);
  const gain = Math.min(-16 - m.i, -1 - m.tp);                      // as loud as -16 LUFS allows without clipping
  level = ', sound ' + (m.i + gain).toFixed(1) + ' LUFS, peak ' + (m.tp + gain).toFixed(1) + ' dB';
  args.push('-i', audio, '-af', 'volume=' + gain.toFixed(2) + 'dB', '-c:a', 'aac', '-b:a', '256k', '-ar', '48000');
}
args.push('-vf', 'fps=30,scale=2560:1440:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-crf', '17', '-preset', 'slow', '-movflags', '+faststart',
  '-t', seconds.toFixed(1), join(OUT, 'raftaar-demo.mp4'));
const r = ff(args);
if (r.status) fail('encode', r);
console.log('wrote ' + join(OUT, 'raftaar-demo.mp4') + ' (' + seconds.toFixed(1) + ' s, 2560x1440, ' + clips.length + ' narration clips' + (music ? ', music' : '') + level + ')');
