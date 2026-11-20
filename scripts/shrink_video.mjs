// The submission form takes an mp4 of at most 10 MB. This makes a copy that fits, from the full-quality video.
// 15 frames a second, so the bits go into sharpness; two-pass, so the busy map scenes get more of them than the still ones.
//
//   node scripts/shrink_video.mjs [in.mp4] [out.mp4] [height]
//   defaults: dist/video/raftaar-demo.mp4 -> dist/video/raftaar-demo-submit.mp4, 1080 lines
import { spawnSync } from 'node:child_process';
import { statSync } from 'node:fs';

const src = process.argv[2] || 'dist/video/raftaar-demo.mp4';
const out = process.argv[3] || src.replace(/\.mp4$/, '-submit.mp4');
const height = +(process.argv[4] || 1080), width = Math.round((height * 16) / 9 / 2) * 2;
const seconds = +spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', src], { encoding: 'utf8' }).stdout;
const LIMIT = 10 * 1000 * 1000, AUDIO = 96000;
let target = 9.3e6;                                           // bytes; a little under the limit, then checked
for (let attempt = 0; attempt < 4; attempt++) {
  const video = Math.floor((target * 8) / seconds - AUDIO);
  for (const pass of [1, 2]) {
    const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', src, '-vf', 'fps=15,scale=' + width + ':' + height + ':flags=lanczos', '-c:v', 'libx264', '-preset', 'veryslow',
      '-b:v', String(video), '-pass', String(pass), '-passlogfile', '/tmp/raftaar-2pass',
      ...(pass === 1 ? ['-an', '-f', 'null', '/dev/null'] : ['-c:a', 'aac', '-b:a', String(AUDIO), '-movflags', '+faststart', out])], { stdio: ['ignore', 'ignore', 'inherit'] });
    if (r.status) process.exit(r.status);
  }
  const size = statSync(out).size;
  if (size < LIMIT) { console.log('wrote ' + out + ': ' + (size / 1e6).toFixed(2) + ' MB, ' + seconds.toFixed(0) + ' s, ' + width + 'x' + height); process.exit(0); }
  target *= 0.93;
}
console.error('could not get under 10 MB');
process.exit(1);
