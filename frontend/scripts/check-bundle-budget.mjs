import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..');
const dist = path.join(root, 'dist');
const manifestPath = path.join(dist, '.vite', 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const initial = new Set();

function visit(key) {
  if (initial.has(key)) return;
  initial.add(key);
  for (const imported of manifest[key]?.imports ?? []) visit(imported);
}

for (const [key, item] of Object.entries(manifest)) {
  if (item.isEntry) visit(key);
}

const forbidden = /(?:xgplayer|dashjs|hls\.js|swiper)/i;
const failures = [];
for (const key of initial) {
  const item = manifest[key];
  if (!item?.file?.endsWith('.js')) continue;
  const bytes = fs.statSync(path.join(dist, item.file)).size;
  if (bytes > 700 * 1024) failures.push(`${item.file}: ${bytes} bytes exceeds 700 KiB`);
  if (forbidden.test(key) || forbidden.test(item.src ?? '')) {
    failures.push(`${item.file}: forbidden media dependency is in the initial graph`);
  }
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`Bundle budget passed for ${initial.size} initial manifest entries.`);
